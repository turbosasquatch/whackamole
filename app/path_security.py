from __future__ import annotations

import os
from pathlib import Path, PurePosixPath
from typing import Iterable, Tuple


def allowed_media_roots() -> Tuple[Path, ...]:
    value = os.getenv("WHACKAMOLE_ALLOWED_MEDIA_ROOTS", "")
    return tuple(Path(part.strip()).resolve(strict=False) for part in value.split(",") if part.strip())


def validate_media_path(value: str, roots: Iterable[Path] | None = None) -> Path:
    resolved = Path(str(value or "")).resolve(strict=False)
    configured = tuple(roots) if roots is not None else allowed_media_roots()
    if configured and not any(_is_within(resolved, root) for root in configured):
        raise ValueError(f"Path is outside configured media roots: {value}")
    return resolved


def safe_join_media(root: str, relative: str) -> Path:
    relative_path = PurePosixPath(str(relative or ""))
    if relative_path.is_absolute() or ".." in relative_path.parts:
        raise ValueError(f"Unsafe relative media path: {relative}")
    resolved_root = validate_media_path(root)
    candidate = resolved_root.joinpath(*relative_path.parts).resolve(strict=False)
    if not _is_within(candidate, resolved_root):
        raise ValueError(f"Media path escapes mapped root: {relative}")
    validate_media_path(str(candidate))
    return candidate


def _is_within(path: Path, root: Path) -> bool:
    try:
        path.relative_to(root.resolve(strict=False))
    except ValueError:
        return False
    return True
