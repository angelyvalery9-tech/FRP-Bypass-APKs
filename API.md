# APK Catalog API

This repository includes a read-only local API for listing APK metadata and checking file integrity. It does not serve APK downloads or provide installation, execution, account-evasion, or FRP-unlock functions.

## Requirements

Python 3.9 or newer. The API uses only the Python standard library.

## Run

From the repository root:

```powershell
python apk_catalog_api.py
```

The server listens only on `127.0.0.1:8000`. An alternate local port can be selected with `--port`, for example `python apk_catalog_api.py --port 8080`.

## Endpoints

### `GET /api/apks`

Returns every `.apk` file found under the repository, except hidden files and files in hidden directories. Each item includes a stable ID derived from its relative path, its name and relative path, byte size, and SHA-256 digest.

```json
{
  "count": 1,
  "items": [
    {
      "id": "stable-sha256-of-relative-path",
      "name": "example.apk",
      "path": "GAM/example.apk",
      "size_bytes": 12345,
      "sha256": "sha256-of-file-contents"
    }
  ]
}
```

An optional `q` parameter filters by a case-insensitive substring of the relative path:

```text
GET /api/apks?q=GAM
```

### `GET /api/apks/{id}`

Returns the metadata for one APK by its 64-character hexadecimal ID. IDs are returned by the list endpoint.

## Errors

Errors are JSON objects with an `error.code` and `error.message`. Invalid IDs return `400`, unknown IDs/endpoints return `404`, and methods other than `GET` return `405`. Filesystem read failures return `500` with a generic message while the cause is logged by the server. Only `GET` is allowed; requests cannot modify repository files.
