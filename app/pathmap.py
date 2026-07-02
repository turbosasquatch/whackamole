from __future__ import annotations

from typing import Iterable

from app.config import PathMapping
from app.path_security import validate_media_path


def map_path(path: str, mappings: Iterable[PathMapping]) -> str:
    normalized_path = _strip_trailing(path)
    if ".." in normalized_path.replace("\\", "/").split("/"):
        raise ValueError(f"Unsafe path traversal in {path}")
    sorted_mappings = sorted(mappings, key=lambda item: len(_strip_trailing(item.source)), reverse=True)
    for mapping in sorted_mappings:
        source = _strip_trailing(mapping.source)
        target = _strip_trailing(mapping.target)
        if normalized_path == source:
            return str(validate_media_path(target))
        if normalized_path.startswith(source + "/"):
            suffix = normalized_path[len(source):]
            return str(validate_media_path(target + suffix))
    raise ValueError(f"No path mapping matched {path}")


def _strip_trailing(value: str) -> str:
    if value == "/":
        return value
    return value.rstrip("/")
