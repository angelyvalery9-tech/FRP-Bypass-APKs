import hashlib
import json
import tempfile
import threading
import unittest
from pathlib import Path
from urllib.error import HTTPError
from urllib.request import Request, urlopen

from apk_catalog_api import ApkCatalogServer, get_apk, list_apks


class ApkCatalogTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.root = Path(self.temp_dir.name)
        self.first_apk = self.root / "GAM" / "first.apk"
        self.first_apk.parent.mkdir()
        self.first_apk.write_bytes(b"sample apk bytes")
        (self.root / "second.APK").write_bytes(b"another apk")
        (self.root / "readme.txt").write_text("not an APK", encoding="utf-8")
        self.hidden_dir = self.root / ".git"
        self.hidden_dir.mkdir()
        (self.hidden_dir / "hidden.apk").write_bytes(b"ignore")

    def tearDown(self):
        self.temp_dir.cleanup()

    def test_list_returns_metadata_and_query_filters(self):
        entries = list_apks(self.root)
        self.assertEqual([entry["path"] for entry in entries], ["GAM/first.apk", "second.APK"])
        first = entries[0]
        self.assertEqual(first["size_bytes"], len(b"sample apk bytes"))
        self.assertEqual(first["sha256"], hashlib.sha256(b"sample apk bytes").hexdigest())
        self.assertEqual(list_apks(self.root, "second")[0]["name"], "second.APK")

    def test_detail_lookup_uses_stable_id(self):
        entry = list_apks(self.root)[0]
        self.assertEqual(get_apk(self.root, entry["id"]), entry)
        self.assertIsNone(get_apk(self.root, "../outside.apk"))

    def test_http_list_detail_and_search(self):
        server = ApkCatalogServer(("127.0.0.1", 0), self.root)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        base_url = "http://127.0.0.1:%d" % server.server_port
        try:
            with urlopen(base_url + "/api/apks") as response:
                listing = json.load(response)
                self.assertEqual(response.status, 200)
                self.assertEqual(listing["count"], 2)
            first_id = listing["items"][0]["id"]
            with urlopen(base_url + "/api/apks/" + first_id) as response:
                self.assertEqual(json.load(response), listing["items"][0])
            with urlopen(base_url + "/api/apks?q=second") as response:
                self.assertEqual(json.load(response)["count"], 1)
        finally:
            server.shutdown()
            server.server_close()
            thread.join()

    def test_invalid_ids_and_write_methods_are_rejected_as_json(self):
        server = ApkCatalogServer(("127.0.0.1", 0), self.root)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        base_url = "http://127.0.0.1:%d" % server.server_port
        try:
            with self.assertRaises(HTTPError) as invalid:
                urlopen(base_url + "/api/apks/not-an-id")
            self.assertEqual(invalid.exception.code, 400)
            self.assertEqual(json.load(invalid.exception)["error"]["code"], "invalid_id")

            request = Request(base_url + "/api/apks", data=b"{}", method="POST")
            with self.assertRaises(HTTPError) as unsupported:
                urlopen(request)
            self.assertEqual(unsupported.exception.code, 405)
            self.assertEqual(unsupported.exception.headers["Allow"], "GET")
            self.assertEqual(json.load(unsupported.exception)["error"]["code"], "method_not_allowed")

            request = Request(base_url + "/api/apks", method="OPTIONS")
            with self.assertRaises(HTTPError) as options:
                urlopen(request)
            self.assertEqual(options.exception.code, 405)
            self.assertEqual(json.load(options.exception)["error"]["code"], "method_not_allowed")
        finally:
            server.shutdown()
            server.server_close()
            thread.join()


if __name__ == "__main__":
    unittest.main()
