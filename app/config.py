from __future__ import annotations

from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional

import yaml
from cryptography.fernet import Fernet, InvalidToken


@dataclass
class QuiConfig:
    url: str = ""
    instance_id: int = 1
    page_limit: int = 200


@dataclass
class UploadAssistantConfig:
    url: str = ""
    tmp_path: str = "/ua-tmp"
    request_timeout_seconds: int = 3600


@dataclass
class PathMapping:
    source: str = "/media/torrents"
    target: str = "/data/torrents"


@dataclass
class WatchConfig:
    exclude_category_terms: List[str] = field(default_factory=lambda: ["cross"])
    exclude_tag_terms: List[str] = field(default_factory=lambda: ["cross-seed"])
    process_existing_on_first_run: bool = False


@dataclass
class SafetyConfig:
    poll_interval_seconds: int = 60
    max_queue_size: int = 250
    max_concurrent_ua_jobs: int = 1
    min_seconds_between_ua_jobs: int = 120
    recheck_cooldown_hours: int = 24
    max_error_retries: int = 3
    error_backoff_minutes: List[int] = field(default_factory=lambda: [15, 60, 360])


@dataclass
class OptionalEndpoint:
    url: str = ""


@dataclass
class AppConfig:
    config_version: int = 1
    host: str = "0.0.0.0"
    port: int = 8383
    qui: QuiConfig = field(default_factory=QuiConfig)
    upload_assistant: UploadAssistantConfig = field(default_factory=UploadAssistantConfig)
    path_mappings: List[PathMapping] = field(default_factory=lambda: [PathMapping()])
    watch: WatchConfig = field(default_factory=WatchConfig)
    safety: SafetyConfig = field(default_factory=SafetyConfig)
    sonarr: OptionalEndpoint = field(default_factory=OptionalEndpoint)
    radarr: OptionalEndpoint = field(default_factory=OptionalEndpoint)
    easycross: OptionalEndpoint = field(default_factory=OptionalEndpoint)


class ConfigManager:
    def __init__(self, config_dir: str = "/config", file_name: str = "config.yaml") -> None:
        self.config_dir = Path(config_dir)
        self.config_path = self.config_dir / file_name
        self.config_dir.mkdir(parents=True, exist_ok=True)

    def load(self) -> AppConfig:
        if not self.config_path.exists():
            cfg = AppConfig()
            self.save(cfg)
            return cfg

        payload = yaml.safe_load(self.config_path.read_text(encoding="utf-8")) or {}
        cfg = AppConfig()
        self._merge_dataclass(cfg, payload)
        cfg.path_mappings = [
            item if isinstance(item, PathMapping) else PathMapping(**item)
            for item in cfg.path_mappings
            if isinstance(item, (dict, PathMapping))
        ] or [PathMapping()]
        return cfg

    def save(self, config: AppConfig) -> None:
        payload = asdict(config)
        self.config_path.write_text(yaml.safe_dump(payload, sort_keys=False), encoding="utf-8")

    def _merge_dataclass(self, target: Any, values: Dict[str, Any]) -> None:
        for key, value in values.items():
            if not hasattr(target, key):
                continue
            current = getattr(target, key)
            if key == "path_mappings" and isinstance(value, list):
                setattr(target, key, [PathMapping(**v) for v in value if isinstance(v, dict)])
            elif hasattr(current, "__dataclass_fields__") and isinstance(value, dict):
                self._merge_dataclass(current, value)
            else:
                setattr(target, key, value)


class SecretStore:
    def __init__(self, config_dir: str = "/config") -> None:
        self.config_dir = Path(config_dir)
        self.key_path = self.config_dir / "secret.key"
        self.secrets_path = self.config_dir / "secrets.yaml"
        self.config_dir.mkdir(parents=True, exist_ok=True)
        self._fernet = Fernet(self._load_or_create_key())

    def has(self, name: str) -> bool:
        return self.get(name) not in (None, "")

    def get(self, name: str) -> Optional[str]:
        encrypted = self._load_payload().get(name)
        if not encrypted:
            return None
        try:
            return self._fernet.decrypt(encrypted.encode("utf-8")).decode("utf-8")
        except (InvalidToken, ValueError):
            return None

    def set(self, name: str, value: str) -> None:
        payload = self._load_payload()
        payload[name] = self._fernet.encrypt(value.encode("utf-8")).decode("utf-8")
        self._save_payload(payload)

    def clear(self, name: str) -> None:
        payload = self._load_payload()
        payload.pop(name, None)
        self._save_payload(payload)

    def _load_or_create_key(self) -> bytes:
        if self.key_path.exists():
            return self.key_path.read_bytes().strip()
        key = Fernet.generate_key()
        self.key_path.write_bytes(key)
        try:
            self.key_path.chmod(0o600)
        except OSError:
            pass
        return key

    def _load_payload(self) -> Dict[str, str]:
        if not self.secrets_path.exists():
            return {}
        data = yaml.safe_load(self.secrets_path.read_text(encoding="utf-8")) or {}
        return {str(k): str(v) for k, v in data.items()}

    def _save_payload(self, payload: Dict[str, str]) -> None:
        self.secrets_path.write_text(yaml.safe_dump(payload, sort_keys=False), encoding="utf-8")
        try:
            self.secrets_path.chmod(0o600)
        except OSError:
            pass


def parse_csv(value: str) -> List[str]:
    return [part.strip() for part in value.split(",") if part.strip()]


def join_csv(values: List[str]) -> str:
    return ", ".join(values)


def parse_path_mappings(value: str) -> List[PathMapping]:
    mappings: List[PathMapping] = []
    for raw_line in value.splitlines():
        line = raw_line.strip()
        if not line:
            continue
        if "=>" in line:
            source, target = line.split("=>", 1)
        elif "=" in line:
            source, target = line.split("=", 1)
        else:
            continue
        source = source.strip()
        target = target.strip()
        if source and target:
            mappings.append(PathMapping(source=source, target=target))
    return mappings or [PathMapping()]


def format_path_mappings(mappings: List[PathMapping]) -> str:
    return "\n".join(f"{item.source} => {item.target}" for item in mappings)

