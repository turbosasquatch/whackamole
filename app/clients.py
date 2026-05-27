from __future__ import annotations

from typing import Any, Dict, List, Optional

import httpx

from app.config import AppConfig


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

    async def list_torrents(self) -> List[Dict[str, Any]]:
        params = {
            "limit": str(max(1, self.config.qui.page_limit)),
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
            torrents = data.get("torrents", []) if isinstance(data, dict) else []
            return torrents if isinstance(torrents, list) else []

    def _headers(self) -> Dict[str, str]:
        return {"X-API-Key": self.api_key or ""}


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
                    line = raw_line.strip()
                    if not line:
                        continue
                    if line.startswith("data:"):
                        line = line[5:].strip()
                    lines.append(line)
        return "\n".join(lines)

    def _headers(self) -> Dict[str, str]:
        return {"Authorization": f"Bearer {self.bearer_token or ''}"}

