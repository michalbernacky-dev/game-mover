import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

import requests

import game_mover_catalog as catalog
import game_mover_flask as backend


class CurseForgeCatalogProviderTest(unittest.TestCase):
    def response(self, payload, status=200):
        response = Mock()
        response.status_code = status
        response.json.return_value = payload
        return response

    def test_search_uses_modpack_version_and_loader_filters(self):
        requester = Mock(return_value=self.response({
            "data": [{
                "id": 123, "name": "Fabric Pack", "slug": "fabric-pack",
                "summary": "A test pack", "downloadCount": 456,
                "dateModified": "2026-08-15T10:00:00Z",
                "authors": [{"name": "Builder"}],
                "links": {"websiteUrl": "https://www.curseforge.com/test"},
                "logo": {"thumbnailUrl": "https://media.example/icon.png"},
                "latestFiles": [{"downloadUrl": "https://secret.example/file.zip"}],
            }],
            "pagination": {"index": 0, "pageSize": 20, "resultCount": 1, "totalCount": 1},
        }))
        provider = catalog.CurseForgeCatalogProvider("api-secret", requester=requester)

        result = provider.search(
            query="fabric", version="1.20.1", loader="fabric",
            sort="popularity", index=0, page_size=20,
        )

        self.assertEqual(result["items"][0]["name"], "Fabric Pack")
        self.assertNotIn("latestFiles", result["items"][0])
        self.assertNotIn("download_url", result["items"][0])
        kwargs = requester.call_args.kwargs
        self.assertEqual(kwargs["params"]["gameId"], 432)
        self.assertEqual(kwargs["params"]["classId"], 4471)
        self.assertEqual(kwargs["params"]["gameVersion"], "1.20.1")
        self.assertEqual(kwargs["params"]["modLoaderType"], 4)
        self.assertEqual(kwargs["headers"]["x-api-key"], "api-secret")
        self.assertEqual(kwargs["headers"]["Cache-Control"], "no-store")

    def test_loader_search_requires_minecraft_version(self):
        provider = catalog.CurseForgeCatalogProvider("api-secret", requester=Mock())
        with self.assertRaisesRegex(catalog.CatalogValidationError, "vyžaduje verzi"):
            provider.search(loader="fabric")

    def test_files_strip_download_url_and_keep_server_pack_metadata(self):
        requester = Mock(return_value=self.response({
            "data": [{
                "id": 789, "displayName": "Server Files", "fileName": "server.zip",
                "releaseType": 1, "fileDate": "2026-08-15T10:00:00Z",
                "fileLength": 1048576, "gameVersions": ["1.20.1", "Fabric"],
                "isServerPack": True, "serverPackFileId": 790,
                "downloadUrl": "https://secret.example/server.zip",
            }],
            "pagination": {"index": 0, "pageSize": 50, "resultCount": 1, "totalCount": 1},
        }))
        provider = catalog.CurseForgeCatalogProvider("api-secret", requester=requester)

        result = provider.files(123, version="1.20.1", loader="fabric")

        self.assertTrue(result["items"][0]["is_server_pack"])
        self.assertEqual(result["items"][0]["server_pack_file_id"], 790)
        self.assertNotIn("download_url", result["items"][0])

    def test_resolves_only_authorized_server_pack_download(self):
        requester = Mock(side_effect=[
            self.response({"data": {
                "id": 123, "gameId": 432, "classId": 4471,
                "name": "Family Pack", "allowModDistribution": True,
            }}),
            self.response({"data": {
                "id": 789, "modId": 123, "isServerPack": False,
                "serverPackFileId": 790,
            }}),
            self.response({"data": {
                "id": 790, "modId": 123, "isServerPack": True,
                "isAvailable": True, "displayName": "Family Server",
                "fileName": "family-server.zip", "fileLength": 1024,
                "hashes": [{"algo": 1, "value": "a" * 40}],
            }}),
            self.response({"data": "https://edge.forgecdn.net/files/1/server.zip"}),
        ])
        provider = catalog.CurseForgeCatalogProvider("api-secret", requester=requester)

        descriptor = provider.resolve_server_pack(123, 789)

        self.assertEqual(descriptor["file_id"], 790)
        self.assertEqual(descriptor["source_file_id"], 789)
        self.assertEqual(
            descriptor["download_url"], "https://edge.forgecdn.net/files/1/server.zip",
        )
        self.assertEqual(requester.call_count, 4)

    def test_rejects_project_that_disallows_third_party_distribution(self):
        requester = Mock(return_value=self.response({"data": {
            "id": 123, "gameId": 432, "classId": 4471,
            "allowModDistribution": False,
        }}))
        provider = catalog.CurseForgeCatalogProvider("api-secret", requester=requester)

        with self.assertRaisesRegex(catalog.CatalogValidationError, "nepovolil"):
            provider.resolve_server_pack(123, 789)

    def test_missing_key_and_upstream_failures_are_safe(self):
        with self.assertRaises(catalog.CatalogNotConfigured):
            catalog.CurseForgeCatalogProvider("").search()
        provider = catalog.CurseForgeCatalogProvider(
            "api-secret", requester=Mock(side_effect=requests.ConnectionError("private")),
        )
        with self.assertRaisesRegex(catalog.CatalogUpstreamError, "není dostupné") as raised:
            provider.search()
        self.assertNotIn("private", str(raised.exception))

    def test_load_key_returns_empty_for_missing_file(self):
        self.assertEqual(catalog.load_curseforge_api_key("/missing/curseforge.key"), "")
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "curseforge.key"
            path.write_text(" secret-key\n", encoding="utf-8")
            self.assertEqual(catalog.load_curseforge_api_key(str(path)), "secret-key")


class CurseForgeCatalogApiTest(unittest.TestCase):
    def setUp(self):
        self.client = backend.app.test_client()

    def local_options(self):
        return {"environ_base": {"REMOTE_ADDR": "127.0.0.1"}}

    def test_status_does_not_expose_key(self):
        provider = Mock(configured=True)
        with patch.object(backend, "curseforge_catalog_provider", return_value=provider):
            response = self.client.get(
                "/minecraft/modpacks/status", **self.local_options(),
            )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json, {
            "provider": "curseforge", "configured": True,
            "server_pack_install": True, "client_install": False, "cached": False,
        })
        self.assertEqual(response.headers["Cache-Control"], "no-store")

    def test_search_proxies_only_validated_provider_result(self):
        provider = Mock()
        provider.search.return_value = {
            "provider": "curseforge", "items": [],
            "pagination": {"index": 0, "pageSize": 20, "resultCount": 0, "totalCount": 0},
            "filters": {},
        }
        with patch.object(backend, "curseforge_catalog_provider", return_value=provider):
            response = self.client.get(
                "/minecraft/modpacks/search?query=fabric&version=1.20.1&loader=fabric",
                **self.local_options(),
            )
        self.assertEqual(response.status_code, 200)
        provider.search.assert_called_once_with(
            query="fabric", version="1.20.1", loader="fabric",
            sort="popularity", index=0, page_size=20,
        )

    def test_missing_key_returns_service_unavailable(self):
        provider = Mock()
        provider.search.side_effect = catalog.CatalogNotConfigured("missing")
        with patch.object(backend, "curseforge_catalog_provider", return_value=provider):
            response = self.client.get(
                "/minecraft/modpacks/search", **self.local_options(),
            )
        self.assertEqual(response.status_code, 503)
        self.assertEqual(response.json["message"], "missing")
        self.assertEqual(response.headers["Cache-Control"], "no-store")

    def test_remote_catalog_requires_read_token(self):
        response = self.client.get(
            "/minecraft/modpacks/status",
            environ_base={"REMOTE_ADDR": "192.0.2.10"},
        )
        self.assertEqual(response.status_code, 403)

    def test_remote_catalog_accepts_valid_read_token(self):
        provider = Mock(configured=True)
        with (
            patch.object(backend, "load_read_token", return_value="read-secret"),
            patch.object(backend, "curseforge_catalog_provider", return_value=provider),
        ):
            response = self.client.get(
                "/minecraft/modpacks/status",
                headers={backend.READ_TOKEN_HEADER: "read-secret"},
                environ_base={"REMOTE_ADDR": "192.0.2.10"},
            )
        self.assertEqual(response.status_code, 200)


if __name__ == "__main__":
    unittest.main()
