       """ APILocal inventory metadata."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any, Optional
from urllib.parse import parse_qs, urlsplit


API_PREFIX = "/api/apks"
ID_PATTERN = re.compile(r"^[0-9a-f]{64}$")


def _raise_walk_error(error: OSError) -> None:
    raise error


def _relative_apk_paths(root: Path) -> list[tuple[str, Path]]:
    root = root.resolve()
    apks: list[tuple[str, Path]] = []
    for directory, subdirectories, filenames in os.walk(root, onerror=_raise_walk_error):
        base = Path(directory)
        subdirectories[:] = [
            name
            for name in subdirectories
            if not name.startswith(".") and not (base / name).is_symlink()
        ]
        for filename in filenames:
            path = base / filename
            if filename.startswith(".") or path.is_symlink() or path.suffix.lower() != ".apk":
                continue
            resolved = path.resolve()
            try:
                relative_path = resolved.relative_to(root).as_posix()
            except ValueError:
                continue
            apks.append((relative_path, resolved))
    return sorted(apks, key=lambda entry: entry[0].casefold())


def _stable_id(relative_path: str) -> str:
    return hashlib.sha256(relative_path.encode("utf-8")).hexdigest()


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as apk_file:
        for chunk in iter(lambda: apk_file.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _entry(relative_path: str, path: Path) -> dict[str, Any]:
    return {
        "id": _stable_id(relative_path),
        "name": path.name,
        "path": relative_path,
        "size_bytes": path.stat().st_size,
        "sha256": _sha256(path),
    }


def list_apks(root: Path, query: str = "") -> list[dict[str, Any]]:
    root = root.resolve()
    normalized_query = query.casefold()
    entries = []
    for relative_path, path in _relative_apk_paths(root):
        if normalized_query and normalized_query not in relative_path.casefold():
            continue
        entries.append(_entry(relative_path, path))
    return entries


def get_apk(root: Path, apk_id: str) -> Optional[dict[str, Any]]:
    if not ID_PATTERN.fullmatch(apk_id):
        return None
    root = root.resolve()
    for relative_path, path in _relative_apk_paths(root):
        if _stable_id(relative_path) == apk_id:
            return _entry(relative_path, path)
    return None


class ApkCatalogHandler(BaseHTTPRequestHandler):
    server_version = "ApkCatalogAPI/1.0"

    @property
    def repository_root(self) -> Path:
        return self.server.repository_root  # type: ignore[attr-defined]

    def _send_json(
        self,
        status: int,
        body: dict[str, Any],
        headers: Optional[dict[str, str]] = None,
    ) -> None:
        encoded = json.dumps(body, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(encoded)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        for name, value in (headers or {}).items():
            self.send_header(name, value)
        self.end_headers()
        self.wfile.write(encoded)

    def _error(self, status: int, code: str, message: str) -> None:
        self._send_json(status, {"error": {"code": code, "message": message}})

    def send_error(self, code: int, message: Optional[str] = None, explain: Optional[str] = None) -> None:
        if code == 501:
            self._method_not_allowed()
            return
        super().send_error(code, message, explain)

    def do_GET(self) -> None:
        parsed = urlsplit(self.path)
        if parsed.path == API_PREFIX:
            params = parse_qs(parsed.query, keep_blank_values=True)
            if set(params) - {"q"} or len(params.get("q", [])) > 1:
                self._error(400, "invalid_query", "Only one optional 'q' parameter is supported.")
                return
            try:
                entries = list_apks(self.repository_root, params.get("q", [""])[0])
            except OSError as error:
                self.log_error("APK catalog read failed: %s", error)
                self._error(500, "catalog_read_failed", "The APK catalog could not be read.")
                return
            self._send_json(200, {"count": len(entries), "items": entries})
            return

        detail_prefix = API_PREFIX + "/"
        if parsed.path.startswith(detail_prefix) and not parsed.query:
            apk_id = parsed.path[len(detail_prefix):]
            if not ID_PATTERN.fullmatch(apk_id):
                self._error(400, "invalid_id", "The APK id is invalid.")
                return
            try:
                entry = get_apk(self.repository_root, apk_id)
            except OSError as error:
                self.log_error("APK detail read failed: %s", error)
                self._error(500, "catalog_read_failed", "The APK metadata could not be read.")
                return
            if entry is None:
                self._error(404, "not_found", "No APK matches the requested id.")
                return
            self._send_json(200, entry)
            return

        self._error(404, "not_found", "The requested endpoint does not exist.")

    def _method_not_allowed(self) -> None:
        self._send_json(
            405,
            {"error": {"code": "method_not_allowed", "message": "This API supports GET requests only."}},
            {"Allow": "GET"},
        )

    def do_POST(self) -> None:
        self._method_not_allowed()

    def do_PUT(self) -> None:
        self._method_not_allowed()

    def do_PATCH(self) -> None:
        self._method_not_allowed()

    def do_DELETE(self) -> None:
        self._method_not_allowed()

    def do_OPTIONS(self) -> None:
        self._method_not_allowed()

    def log_message(self, format: str, *args: object) -> None:
        print("%s - %s" % (self.address_string(), format % args))


class ApkCatalogServer(ThreadingHTTPServer):
    def __init__(self, address: tuple[str, int], repository_root: Path):
        self.repository_root = repository_root.resolve()
        super().__init__(address, ApkCatalogHandler)


def main() -> None:
    parser = argparse.ArgumentParser(description="Serve a read-only APK catalog on localhost.")
    parser.add_argument("--port", type=int, default=8000, help="Local HTTP port (default: 8000)")
    args = parser.parse_args()
    if not 0 < args.port < 65536:
        parser.error("--port must be between 1 and 65535")

    repository_root = Path(__file__).resolve().parent
    server = ApkCatalogServer(("127.0.0.1", args.port), repository_root)
    print("APK catalog API serving on http://127.0.0.1:%d" % args.port)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nStopping APK catalog API.")
    finally:
        server.server_close()


if __name__ == "__main__":
    main()
