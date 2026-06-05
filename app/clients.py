from __future__ import annotations

from typing import Any, AsyncIterator, Dict, List, Optional
from urllib.parse import quote

import httpx

from app.config import AppConfig
from app.ua_logs import normalize_ua_event_line


class QuiClient:
    def __init__(self, config: AppConfig, api_key: Optional[str]) -> None:
        self.config = config
        self.api_key = api_key

    async def health(self) -> Dict[str, Any]:
        async with httpx.AsyncClient(timeout=10) as client:
            response = await client.get(f"{self.config.qui.url.rstrip('/')}/api/auth/check-setup")
            response.raise_for_status()
            return response.json()

    async def list_instances(self) -> List[Dict[str, Any]]:
        async with httpx.AsyncClient(timeout=15) as client:
            response = await client.get(
                f"{self.config.qui.url.rstrip('/')}/api/instances/",
                headers=self._headers(),
            )
            response.raise_for_status()
            data = response.json()
            return data if isinstance(data, list) else []

    async def list_torrents_page(self, page: int = 0, limit: Optional[int] = None) -> Dict[str, Any]:
        page_limit = limit or self.config.qui.page_limit
        params = {
            "limit": str(max(1, page_limit)),
            "page": str(max(0, page)),
            "sort": "added_on",
            "order": "desc",
        }
        async with httpx.AsyncClient(timeout=30) as client:
            response = await client.get(
                f"{self.config.qui.url.rstrip('/')}/api/instances/{self.config.qui.instance_id}/torrents/",
                headers=self._headers(),
                params=params,
            )
            response.raise_for_status()
            data = response.json()
            return data if isinstance(data, dict) else {"torrents": data if isinstance(data, list) else []}

    async def list_torrents(self) -> List[Dict[str, Any]]:
        limit = max(1, self.config.qui.page_limit)
        max_pages = _max_qui_poll_pages(self.config)
        page = 0
        torrents: List[Dict[str, Any]] = []
        seen_hashes = set()
        while page < max_pages:
            data = await self.list_torrents_page(page=page, limit=limit)
            rows = data.get("torrents", [])
            rows = rows if isinstance(rows, list) else []
            for row in rows:
                torrent_hash = str(row.get("hash") or "")
                if torrent_hash and torrent_hash in seen_hashes:
                    continue
                if torrent_hash:
                    seen_hashes.add(torrent_hash)
                torrents.append(row)

            total = int(data.get("total") or 0)
            has_more = bool(data.get("hasMore"))
            page += 1
            if not rows:
                break
            if total and len(torrents) >= total:
                break
            if not has_more and len(rows) < limit:
                break
        return torrents

    async def list_torrent_files(self, torrent_hash: str, refresh: bool = False) -> List[Dict[str, Any]]:
        params = {"refresh": "true"} if refresh else None
        async with httpx.AsyncClient(timeout=30) as client:
            response = await client.get(
                f"{self.config.qui.url.rstrip('/')}/api/instances/{self.config.qui.instance_id}/torrents/{torrent_hash}/files",
                headers=self._headers(),
                params=params,
            )
            response.raise_for_status()
            data = response.json()
            files = data if isinstance(data, list) else []
            return [
                {
                    **file_info,
                    "index": index,
                }
                for index, file_info in enumerate(files)
                if isinstance(file_info, dict)
            ]

    async def download_torrent_file(self, torrent_hash: str, file_index: int, max_bytes: int = 262144) -> bytes:
        timeout = httpx.Timeout(30)
        content = bytearray()
        async with httpx.AsyncClient(timeout=timeout) as client:
            async with client.stream(
                "GET",
                f"{self.config.qui.url.rstrip('/')}/api/instances/{self.config.qui.instance_id}/torrents/{torrent_hash}/files/{file_index}/download",
                headers=self._headers(),
            ) as response:
                response.raise_for_status()
                async for chunk in response.aiter_bytes():
                    content.extend(chunk)
                    if len(content) > max_bytes:
                        raise ValueError(f"Torrent file exceeds {max_bytes} bytes")
        return bytes(content)

    async def torrent_file_mediainfo(self, torrent_hash: str, file_index: int) -> Dict[str, Any]:
        async with httpx.AsyncClient(timeout=30) as client:
            response = await client.get(
                f"{self.config.qui.url.rstrip('/')}/api/instances/{self.config.qui.instance_id}/torrents/{torrent_hash}/files/{file_index}/mediainfo",
                headers=self._headers(),
            )
            response.raise_for_status()
            data = response.json()
            return data if isinstance(data, dict) else {}

    def _headers(self) -> Dict[str, str]:
        return {"X-API-Key": self.api_key or ""}


def _max_qui_poll_pages(config: AppConfig) -> int:
    try:
        return max(1, int(config.safety.max_qui_poll_pages or 100))
    except (AttributeError, TypeError, ValueError):
        return 100


class UploadAssistantClient:
    def __init__(self, config: AppConfig, bearer_token: Optional[str]) -> None:
        self.config = config
        self.bearer_token = bearer_token

    async def health(self) -> Dict[str, Any]:
        async with httpx.AsyncClient(timeout=10) as client:
            response = await client.get(f"{self.config.upload_assistant.url.rstrip('/')}/api/health")
            response.raise_for_status()
            return response.json()

    async def browse_roots(self) -> Dict[str, Any]:
        async with httpx.AsyncClient(timeout=10) as client:
            response = await client.get(
                f"{self.config.upload_assistant.url.rstrip('/')}/api/browse_roots",
                headers=self._headers(),
            )
            response.raise_for_status()
            return response.json()

    async def execute_site_check(self, path: str, args: str, session_id: str) -> str:
        payload = {"path": path, "args": args, "session_id": session_id}
        timeout = httpx.Timeout(self.config.upload_assistant.request_timeout_seconds)
        lines: List[str] = []
        async with httpx.AsyncClient(timeout=timeout) as client:
            async with client.stream(
                "POST",
                f"{self.config.upload_assistant.url.rstrip('/')}/api/execute",
                headers={**self._headers(), "Accept": "text/event-stream"},
                json=payload,
            ) as response:
                response.raise_for_status()
                async for raw_line in response.aiter_lines():
                    line = normalize_ua_event_line(raw_line)
                    if not line:
                        continue
                    lines.append(line)
        return "\n".join(lines)

    async def execute_upload_stream(self, path: str, args: str, session_id: str) -> AsyncIterator[str]:
        payload = {"path": path, "args": args, "session_id": session_id}
        timeout = httpx.Timeout(self.config.upload_assistant.request_timeout_seconds)
        async with httpx.AsyncClient(timeout=timeout) as client:
            async with client.stream(
                "POST",
                f"{self.config.upload_assistant.url.rstrip('/')}/api/execute",
                headers={**self._headers(), "Accept": "text/event-stream"},
                json=payload,
            ) as response:
                response.raise_for_status()
                async for chunk in response.aiter_text():
                    if chunk:
                        yield chunk

    async def send_input(self, session_id: str, user_input: str) -> Dict[str, Any]:
        async with httpx.AsyncClient(timeout=30) as client:
            response = await client.post(
                f"{self.config.upload_assistant.url.rstrip('/')}/api/input",
                headers=self._headers(),
                json={"session_id": session_id, "input": user_input},
            )
            response.raise_for_status()
            data = response.json()
            return data if isinstance(data, dict) else {"success": True}

    async def kill_session(self, session_id: str) -> Dict[str, Any]:
        async with httpx.AsyncClient(timeout=30) as client:
            response = await client.post(
                f"{self.config.upload_assistant.url.rstrip('/')}/api/kill",
                headers=self._headers(),
                json={"session_id": session_id},
            )
            response.raise_for_status()
            data = response.json()
            return data if isinstance(data, dict) else {"success": True}

    def _headers(self) -> Dict[str, str]:
        return {"Authorization": f"Bearer {self.bearer_token or ''}"}


class SrrdbClient:
    def __init__(self, timeout_seconds: int = 10) -> None:
        self.timeout_seconds = timeout_seconds
        self.url = "https://api.srrdb.com/v1"

    async def details(self, release_name: str) -> Dict[str, Any] | List[Any]:
        async with httpx.AsyncClient(timeout=self.timeout_seconds) as client:
            response = await client.get(f"{self.url}/details/{quote(release_name, safe='')}")
            response.raise_for_status()
            data = response.json()
            return data if isinstance(data, (dict, list)) else {}


class BaseArrClient:
    def __init__(self, url: str, api_key: Optional[str], timeout_seconds: int = 45) -> None:
        self.url = url.rstrip("/")
        self.api_key = api_key
        self.timeout_seconds = timeout_seconds

    async def system_status(self) -> Dict[str, Any]:
        return await self._get("/api/v3/system/status")

    async def list_indexers(self) -> List[Dict[str, Any]]:
        data = await self._get("/api/v3/indexer")
        return data if isinstance(data, list) else []

    async def _get(self, path: str, params: Optional[Dict[str, Any]] = None) -> Any:
        timeout = httpx.Timeout(self.timeout_seconds, connect=min(10, self.timeout_seconds))
        async with httpx.AsyncClient(timeout=timeout) as client:
            response = await client.get(f"{self.url}{path}", headers=self._headers(), params=params)
            response.raise_for_status()
            return response.json()

    def _headers(self) -> Dict[str, str]:
        return {"X-Api-Key": self.api_key or ""}


class ProfilarrClient:
    def __init__(self, url: str, api_key: Optional[str], timeout_seconds: int = 30) -> None:
        self.url = url.rstrip("/")
        self.api_key = api_key
        self.timeout_seconds = timeout_seconds

    async def health(self) -> Dict[str, Any]:
        async with httpx.AsyncClient(timeout=min(10, self.timeout_seconds)) as client:
            response = await client.get(f"{self.url}/api/v1/health")
            response.raise_for_status()
            data = response.json()
            return data if isinstance(data, dict) else {}

    async def status(self) -> Dict[str, Any]:
        timeout = httpx.Timeout(self.timeout_seconds, connect=min(10, self.timeout_seconds))
        async with httpx.AsyncClient(timeout=timeout) as client:
            response = await client.get(f"{self.url}/api/v1/status", headers=self._headers())
            response.raise_for_status()
            data = response.json()
            return data if isinstance(data, dict) else {}

    def _headers(self) -> Dict[str, str]:
        return {"X-Api-Key": self.api_key or ""}


class SonarrClient(BaseArrClient):
    async def list_series(self) -> List[Dict[str, Any]]:
        data = await self._get("/api/v3/series")
        return data if isinstance(data, list) else []

    async def list_episodes(self, series_id: int, season_number: Optional[int] = None) -> List[Dict[str, Any]]:
        params: Dict[str, Any] = {"seriesId": series_id}
        if season_number is not None:
            params["seasonNumber"] = season_number
        data = await self._get("/api/v3/episode", params=params)
        return data if isinstance(data, list) else []

    async def search_releases(
        self,
        *,
        series_id: Optional[int] = None,
        season_number: Optional[int] = None,
        episode_id: Optional[int] = None,
    ) -> List[Dict[str, Any]]:
        params: Dict[str, Any] = {}
        if episode_id is not None:
            params["episodeId"] = episode_id
        else:
            params["seriesId"] = series_id
            params["seasonNumber"] = season_number
        data = await self._get("/api/v3/release", params=params)
        return data if isinstance(data, list) else []


class RadarrClient(BaseArrClient):
    async def list_movies(self) -> List[Dict[str, Any]]:
        data = await self._get("/api/v3/movie")
        return data if isinstance(data, list) else []

    async def search_releases(self, movie_id: int) -> List[Dict[str, Any]]:
        data = await self._get("/api/v3/release", params={"movieId": movie_id})
        return data if isinstance(data, list) else []
