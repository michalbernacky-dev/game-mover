"""Live modpack catalogs and host-only server-pack resolution.

CurseForge data is deliberately never persisted or cached. The API key stays in
the host service, public responses are reduced to fields needed by the UI, and
download URLs are resolved only within an authorized server installation.
"""

from abc import ABC, abstractmethod
import os
import re

import requests


CURSEFORGE_API_URL = "https://api.curseforge.com"
CURSEFORGE_MINECRAFT_GAME_ID = 432
CURSEFORGE_MODPACKS_CLASS_ID = 4471
CURSEFORGE_PAGE_SIZE_MAX = 50
CURSEFORGE_LOADER_TYPES = {
    "any": 0,
    "forge": 1,
    "fabric": 4,
    "quilt": 5,
    "neoforge": 6,
}
CURSEFORGE_SORT_FIELDS = {
    "featured": 1,
    "popularity": 2,
    "updated": 3,
    "name": 4,
    "downloads": 6,
    "released": 11,
    "rating": 12,
}
GAME_VERSION_RE = re.compile(r"[0-9A-Za-z][0-9A-Za-z.+_-]{0,31}")


class CatalogError(RuntimeError):
    """A safe error suitable for returning through the Game Mover API."""


class CatalogNotConfigured(CatalogError):
    pass


class CatalogValidationError(CatalogError):
    pass


class CatalogUpstreamError(CatalogError):
    pass


class ModpackCatalogProvider(ABC):
    @abstractmethod
    def search(self, **filters):
        raise NotImplementedError

    @abstractmethod
    def files(self, project_id, **filters):
        raise NotImplementedError

    @abstractmethod
    def resolve_server_pack(self, project_id, file_id):
        raise NotImplementedError


def load_curseforge_api_key(path):
    try:
        with open(path, "r", encoding="utf-8") as key_file:
            return key_file.read().strip()
    except (OSError, UnicodeError):
        return ""


def normalize_catalog_filters(raw, *, include_query=True):
    raw = raw or {}
    filters = {}
    if include_query:
        query = str(raw.get("query", "")).strip()
        if len(query) > 100:
            raise CatalogValidationError("Hledaný text může mít nejvýše 100 znaků")
        filters["query"] = query

    version = str(raw.get("version", "")).strip()
    if version and not GAME_VERSION_RE.fullmatch(version):
        raise CatalogValidationError("Neplatná verze Minecraftu")
    filters["version"] = version

    loader = str(raw.get("loader", "any")).strip().lower() or "any"
    if loader not in CURSEFORGE_LOADER_TYPES:
        raise CatalogValidationError("Nepodporovaný Minecraft loader")
    filters["loader"] = loader

    if include_query:
        sort = str(raw.get("sort", "popularity")).strip().lower() or "popularity"
        if sort not in CURSEFORGE_SORT_FIELDS:
            raise CatalogValidationError("Nepodporované řazení katalogu")
        filters["sort"] = sort

    try:
        index = int(raw.get("index", 0))
        page_size = int(raw.get("page_size", 20))
    except (TypeError, ValueError) as error:
        raise CatalogValidationError("Neplatné stránkování katalogu") from error
    if index < 0 or page_size < 1 or page_size > CURSEFORGE_PAGE_SIZE_MAX:
        raise CatalogValidationError("Neplatné stránkování katalogu")
    if index + page_size > 10_000:
        raise CatalogValidationError(
            "CurseForge dovoluje procházet nejvýše 10 000 výsledků"
        )
    filters["index"] = index
    filters["page_size"] = page_size
    return filters


def _author_names(project):
    authors = project.get("authors") if isinstance(project.get("authors"), list) else []
    return [
        str(author.get("name", "")).strip()
        for author in authors
        if isinstance(author, dict) and str(author.get("name", "")).strip()
    ]


def public_modpack(project):
    logo = project.get("logo") if isinstance(project.get("logo"), dict) else {}
    links = project.get("links") if isinstance(project.get("links"), dict) else {}
    return {
        "id": int(project.get("id", 0)),
        "name": str(project.get("name", "")),
        "slug": str(project.get("slug", "")),
        "summary": str(project.get("summary", "")),
        "authors": _author_names(project),
        "download_count": int(project.get("downloadCount") or 0),
        "date_modified": str(project.get("dateModified", "")),
        "website_url": str(links.get("websiteUrl", "")),
        "thumbnail_url": str(logo.get("thumbnailUrl", "")),
    }


def public_modpack_file(item):
    return {
        "id": int(item.get("id", 0)),
        "display_name": str(item.get("displayName", "")),
        "file_name": str(item.get("fileName", "")),
        "release_type": int(item.get("releaseType") or 0),
        "file_date": str(item.get("fileDate", "")),
        "file_length": int(item.get("fileLength") or 0),
        "game_versions": [
            str(version) for version in item.get("gameVersions", [])
            if isinstance(version, str)
        ],
        "is_server_pack": bool(item.get("isServerPack")),
        "server_pack_file_id": (
            int(item["serverPackFileId"]) if item.get("serverPackFileId") else None
        ),
    }


class CurseForgeCatalogProvider(ModpackCatalogProvider):
    def __init__(self, api_key, *, requester=None, base_url=CURSEFORGE_API_URL):
        self.api_key = str(api_key or "").strip()
        if requester is None:
            session = requests.Session()
            # CurseForge forbids concealing API access through a proxy. Do not
            # inherit HTTP(S)_PROXY from the service environment.
            session.trust_env = False
            requester = session.get
            self._session = session
        else:
            self._session = None
        self.requester = requester
        self.base_url = base_url.rstrip("/")

    @property
    def configured(self):
        return bool(self.api_key)

    def _get_data(self, path, params, expected_type):
        if not self.configured:
            raise CatalogNotConfigured("CurseForge API klíč není nakonfigurovaný")
        try:
            response = self.requester(
                f"{self.base_url}{path}",
                params=params,
                headers={
                    "Accept": "application/json",
                    "Cache-Control": "no-store",
                    "x-api-key": self.api_key,
                },
                timeout=(3.05, 20),
            )
        except requests.RequestException as error:
            raise CatalogUpstreamError("CurseForge API není dostupné") from error
        if response.status_code == 401 or response.status_code == 403:
            raise CatalogUpstreamError("CurseForge API klíč byl odmítnut")
        if response.status_code != 200:
            raise CatalogUpstreamError(
                f"CurseForge API vrátilo HTTP {response.status_code}"
            )
        try:
            payload = response.json()
        except ValueError as error:
            raise CatalogUpstreamError(
                "CurseForge API vrátilo neplatnou odpověď"
            ) from error
        if not isinstance(payload, dict) or not isinstance(payload.get("data"), expected_type):
            raise CatalogUpstreamError("CurseForge API vrátilo neplatná data")
        return payload

    def _get(self, path, params):
        payload = self._get_data(path, params, list)
        if not all(isinstance(item, dict) for item in payload["data"]):
            raise CatalogUpstreamError("CurseForge API vrátilo neplatná data")
        return payload

    def _get_object(self, path):
        return self._get_data(path, {}, dict)["data"]

    @staticmethod
    def _positive_id(value, label):
        try:
            value = int(value)
        except (TypeError, ValueError) as error:
            raise CatalogValidationError(f"Neplatné {label}") from error
        if value < 1:
            raise CatalogValidationError(f"Neplatné {label}")
        return value

    def search(self, **raw_filters):
        filters = normalize_catalog_filters(raw_filters)
        params = {
            "gameId": CURSEFORGE_MINECRAFT_GAME_ID,
            "classId": CURSEFORGE_MODPACKS_CLASS_ID,
            "sortField": CURSEFORGE_SORT_FIELDS[filters["sort"]],
            "sortOrder": "desc" if filters["sort"] != "name" else "asc",
            "index": filters["index"],
            "pageSize": filters["page_size"],
        }
        if filters["query"]:
            params["searchFilter"] = filters["query"]
        if filters["version"]:
            params["gameVersion"] = filters["version"]
        if filters["loader"] != "any":
            if not filters["version"]:
                raise CatalogValidationError(
                    "Filtr loaderu vyžaduje verzi Minecraftu"
                )
            params["modLoaderType"] = CURSEFORGE_LOADER_TYPES[filters["loader"]]
        payload = self._get("/v1/mods/search", params)
        return {
            "provider": "curseforge",
            "items": [public_modpack(item) for item in payload["data"]],
            "pagination": (
                dict(payload["pagination"])
                if isinstance(payload.get("pagination"), dict) else {}
            ),
            "filters": filters,
        }

    def files(self, project_id, **raw_filters):
        project_id = self._positive_id(project_id, "ID modpacku")
        filters = normalize_catalog_filters(raw_filters, include_query=False)
        params = {
            "index": filters["index"],
            "pageSize": filters["page_size"],
        }
        if filters["version"]:
            params["gameVersion"] = filters["version"]
        if filters["loader"] != "any":
            params["modLoaderType"] = CURSEFORGE_LOADER_TYPES[filters["loader"]]
        payload = self._get(f"/v1/mods/{project_id}/files", params)
        return {
            "provider": "curseforge",
            "project_id": project_id,
            "items": [public_modpack_file(item) for item in payload["data"]],
            "pagination": (
                dict(payload["pagination"])
                if isinstance(payload.get("pagination"), dict) else {}
            ),
            "filters": filters,
        }

    def resolve_server_pack(self, project_id, file_id):
        """Resolve a selected release to its server pack without exposing its URL."""
        project_id = self._positive_id(project_id, "ID modpacku")
        file_id = self._positive_id(file_id, "ID souboru")
        project = self._get_object(f"/v1/mods/{project_id}")
        if (
            int(project.get("id") or 0) != project_id
            or int(project.get("gameId") or 0) != CURSEFORGE_MINECRAFT_GAME_ID
            or int(project.get("classId") or 0) != CURSEFORGE_MODPACKS_CLASS_ID
        ):
            raise CatalogValidationError("Projekt není Minecraft modpack")
        if project.get("allowModDistribution") is False:
            raise CatalogValidationError("Autor modpacku nepovolil distribuci třetím stranám")

        selected = self._get_object(f"/v1/mods/{project_id}/files/{file_id}")
        if int(selected.get("id") or 0) != file_id:
            raise CatalogUpstreamError("CurseForge vrátilo jiný soubor")
        if selected.get("isServerPack"):
            server_file_id = file_id
        else:
            server_file_id = self._positive_id(
                selected.get("serverPackFileId"), "ID server packu",
            )
        server_file = (
            selected if server_file_id == file_id
            else self._get_object(f"/v1/mods/{project_id}/files/{server_file_id}")
        )
        if (
            int(server_file.get("id") or 0) != server_file_id
            or int(server_file.get("modId") or project_id) != project_id
            or not server_file.get("isServerPack")
            or server_file.get("isAvailable") is False
        ):
            raise CatalogValidationError("Vybraný soubor nemá dostupný server pack")
        file_name = str(server_file.get("fileName", "")).strip()
        if not file_name.lower().endswith(".zip"):
            raise CatalogValidationError("Server pack není podporovaný ZIP archiv")

        download_url = self._get_data(
            f"/v1/mods/{project_id}/files/{server_file_id}/download-url", {}, str,
        )["data"].strip()
        if not download_url:
            raise CatalogValidationError("CurseForge neposkytl URL server packu")
        hashes = server_file.get("hashes") if isinstance(server_file.get("hashes"), list) else []
        return {
            "provider": "curseforge",
            "project_id": project_id,
            "source_file_id": file_id,
            "file_id": server_file_id,
            "project_name": str(project.get("name", "")),
            "display_name": str(server_file.get("displayName", "")),
            "file_name": file_name,
            "file_length": int(server_file.get("fileLength") or 0),
            "hashes": [
                {"algorithm": int(item.get("algo") or 0), "value": str(item.get("value", ""))}
                for item in hashes if isinstance(item, dict)
            ],
            # Internal only: never return this descriptor through a catalog route.
            "download_url": download_url,
        }
