from __future__ import annotations

import os
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, Mapping, Optional, Sequence, Tuple

import httpx


ROOT_DIR = Path(__file__).resolve().parents[1]
DEFAULT_LIVE_ENV_PATH = ROOT_DIR / ".codex" / "live-api.env"


@dataclass(frozen=True)
class LiveApiSettings:
    base_url: str
    fallback_url: str
    token: str

    def base_urls(self) -> Sequence[str]:
        urls = []
        seen = set()
        for value in (self.base_url, self.fallback_url):
            url = str(value or "").rstrip("/")
            if not url or url in seen:
                continue
            seen.add(url)
            urls.append(url)
        return urls


def load_env_file(path: Path = DEFAULT_LIVE_ENV_PATH) -> Dict[str, str]:
    if not path.exists():
        return {}
    values: Dict[str, str] = {}
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("export "):
            line = line[len("export ") :].strip()
        if "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", key):
            continue
        values[key] = _strip_shell_value(value.strip())
    return values


def live_api_settings(
    *,
    base_url: str = "",
    fallback_url: str = "",
    token: str = "",
    token_env: str = "WHACKAMOLE_API_TOKEN",
    env_path: Path = DEFAULT_LIVE_ENV_PATH,
    environ: Optional[Mapping[str, str]] = None,
    default_base_url: str = "",
) -> LiveApiSettings:
    env = dict(os.environ if environ is None else environ)
    file_env = load_env_file(env_path)

    def value(name: str) -> str:
        return str(env.get(name) or file_env.get(name) or "")

    return LiveApiSettings(
        base_url=str(base_url or value("WHACKAMOLE_API_BASE_URL") or default_base_url).rstrip("/"),
        fallback_url=str(fallback_url or value("WHACKAMOLE_API_FALLBACK_URL")).rstrip("/"),
        token=str(token or value(token_env)),
    )


def get_json_from_any(
    *,
    base_urls: Sequence[str],
    token: str,
    path: str,
    params: Optional[Mapping[str, Any]] = None,
    timeout: float = 30.0,
    client_factory: Any = httpx.Client,
) -> Tuple[Dict[str, Any], str]:
    last_error: Optional[BaseException] = None
    for base_url in base_urls:
        try:
            with client_factory(base_url=base_url, timeout=timeout, headers=auth_headers(token)) as client:
                response = client.get(path, params=params)
                response.raise_for_status()
                payload = response.json()
                if not isinstance(payload, dict):
                    raise ValueError(f"Expected JSON object from {path}")
                return payload, base_url
        except httpx.HTTPStatusError:
            raise
        except (httpx.TransportError, ValueError) as exc:
            last_error = exc
            continue
    if last_error:
        raise RuntimeError(f"API request failed for {path}: {last_error}") from last_error
    raise RuntimeError(f"No base URL configured for {path}")


def auth_headers(token: str) -> Dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


def _strip_shell_value(value: str) -> str:
    if len(value) >= 2 and value[0] == value[-1] and value[0] in {"'", '"'}:
        return value[1:-1]
    return value
