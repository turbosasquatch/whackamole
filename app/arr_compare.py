from __future__ import annotations

import re
from dataclasses import asdict, dataclass
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple

import httpx

from app.clients import RadarrClient, SonarrClient
from app.config import AppConfig, SecretStore
from app.ua_logs import normalize_ua_log


TRACKER_ALIASES: Dict[str, Sequence[str]] = {
    "IHD": ("ihd", "infinityhd"),
    "DP": ("dp", "darkpeers", "darkpeer"),
    "ULCX": ("ulcx", "uploadcx", "upload.cx"),
    "DC": ("dc", "digitalcore"),
    "TL": ("tl", "torrentleech"),
    "IPT": ("ipt", "iptorrents"),
    "SP": ("sp", "seedpool"),
}

SOURCE_LABELS = {
    "web": "WEB",
    "bluray_remux": "BluRay Remux",
    "bluray_encode": "BluRay Encode",
    "other": "Other",
}

HDR_LABELS = {
    3: "DV/HDR10+",
    2: "HDR",
    0: "SDR",
}


@dataclass
class ReleaseTraits:
    title: str
    resolution: str = ""
    source: str = "other"
    hdr_rank: int = 0
    audio_channels: float = 0.0
    season: Optional[int] = None
    episode: Optional[int] = None
    season_pack: bool = False

    @property
    def source_label(self) -> str:
        return SOURCE_LABELS.get(self.source, self.source)

    @property
    def hdr_label(self) -> str:
        return HDR_LABELS.get(self.hdr_rank, "SDR")

    @property
    def is_comparable(self) -> bool:
        return bool(self.resolution and self.source != "other")


@dataclass
class MediaIdentity:
    kind: str
    title: str = ""
    year: Optional[int] = None
    tmdb_id: Optional[int] = None
    tvdb_id: Optional[int] = None
    imdb_id: str = ""
    season: Optional[int] = None
    episode: Optional[int] = None


async def compare_item_with_arr(
    *,
    item_name: str,
    ua_log: str,
    passed_trackers: Sequence[str],
    cfg: AppConfig,
    secrets: SecretStore,
) -> Dict[str, Any]:
    local_traits = parse_release_traits(item_name)
    identity = parse_media_identity(ua_log, item_name)
    result: Dict[str, Any] = {
        "version": 1,
        "status": "manual_review",
        "reason": "",
        "source": identity.kind,
        "local_traits": _traits_payload(local_traits),
        "media": _media_payload(identity),
        "decisions": [],
        "errors": [],
    }

    if not passed_trackers:
        result["status"] = "skipped"
        result["reason"] = "UA did not pass any trackers."
        return result

    if not local_traits.is_comparable:
        result["reason"] = "Whackamole could not parse enough release traits for Arr comparison."
        result["decisions"] = _manual_decisions(passed_trackers, result["reason"])
        return result

    try:
        if identity.kind == "sonarr":
            releases, indexers = await _sonarr_releases(identity, local_traits, cfg, secrets)
        elif identity.kind == "radarr":
            releases, indexers = await _radarr_releases(identity, cfg, secrets)
        else:
            result["reason"] = "Whackamole could not determine whether this item belongs to Sonarr or Radarr."
            result["decisions"] = _manual_decisions(passed_trackers, result["reason"])
            return result
    except httpx.TimeoutException:
        result["reason"] = "Arr comparison timed out."
        result["errors"].append(result["reason"])
        result["decisions"] = _manual_decisions(passed_trackers, result["reason"])
        return result
    except Exception as exc:
        result["reason"] = f"Arr comparison unavailable: {str(exc)[:180]}"
        result["errors"].append(result["reason"])
        result["decisions"] = _manual_decisions(passed_trackers, result["reason"])
        return result

    decisions = evaluate_tracker_decisions(
        passed_trackers=passed_trackers,
        local_traits=local_traits,
        releases=releases,
        configured_indexers=indexers,
    )
    result["decisions"] = decisions
    result["status"], result["reason"] = summarize_decisions(decisions)
    return result


def parse_media_identity(log: str, item_name: str) -> MediaIdentity:
    text = normalize_ua_log(log)
    title, year = _extract_title_year(text, item_name)
    tmdb_match = re.search(r"themoviedb\.org/(tv|movie)/(\d+)", text, flags=re.IGNORECASE)
    tmdb_kind = tmdb_match.group(1).lower() if tmdb_match else ""
    tmdb_id = int(tmdb_match.group(2)) if tmdb_match else None
    tvdb_match = re.search(r"(?:TVDB:|thetvdb\.com).*?(?:id=|series/)?(\d{3,})", text, flags=re.IGNORECASE)
    imdb_match = re.search(r"imdb\.com/title/(tt\d+)", text, flags=re.IGNORECASE)
    category_match = re.search(r"Category:\s*([^\n\r]+)", text, flags=re.IGNORECASE)
    category = category_match.group(1).strip().lower() if category_match else ""
    traits = parse_release_traits(item_name)

    if "tv" in category or tmdb_kind == "tv" or tvdb_match or traits.season is not None:
        kind = "sonarr"
    elif "movie" in category or tmdb_kind == "movie" or tmdb_id or imdb_match:
        kind = "radarr"
    else:
        kind = "unknown"

    return MediaIdentity(
        kind=kind,
        title=title,
        year=year,
        tmdb_id=tmdb_id,
        tvdb_id=int(tvdb_match.group(1)) if tvdb_match else None,
        imdb_id=imdb_match.group(1) if imdb_match else "",
        season=traits.season,
        episode=traits.episode,
    )


def parse_release_traits(title: str, quality_name: str = "") -> ReleaseTraits:
    text = f"{title} {quality_name}".replace("_", ".")
    normalized = text.lower()
    season, episode = _parse_season_episode(text)
    return ReleaseTraits(
        title=title,
        resolution=_parse_resolution(normalized),
        source=_parse_source(normalized),
        hdr_rank=_parse_hdr_rank(normalized),
        audio_channels=_parse_audio_channels(text),
        season=season,
        episode=episode,
        season_pack=season is not None and episode is None,
    )


def evaluate_tracker_decisions(
    *,
    passed_trackers: Sequence[str],
    local_traits: ReleaseTraits,
    releases: Sequence[Dict[str, Any]],
    configured_indexers: Sequence[Dict[str, Any]],
) -> List[Dict[str, Any]]:
    torrent_releases = [release for release in releases if str(release.get("protocol", "")).lower() == "torrent"]
    configured = {
        canonical_tracker(str(indexer.get("name", "")))
        for indexer in configured_indexers
        if str(indexer.get("protocol", "")).lower() == "torrent"
    }
    configured.discard(None)
    decisions: List[Dict[str, Any]] = []

    for tracker in passed_trackers:
        canon = canonical_tracker(tracker)
        if not canon:
            decisions.append(
                {
                    "tracker": tracker,
                    "status": "manual_review",
                    "reason": "No tracker alias is configured for Arr comparison.",
                    "matched_count": 0,
                    "best_release": None,
                }
            )
            continue
        if canon not in configured:
            decisions.append(
                {
                    "tracker": tracker,
                    "status": "manual_review",
                    "reason": "No matching torrent indexer is configured in Arr.",
                    "matched_count": 0,
                    "best_release": None,
                }
            )
            continue

        matches = [
            release
            for release in torrent_releases
            if canonical_tracker(str(release.get("indexer", ""))) == canon
        ]
        same_lane = [
            (release, parse_release_traits(str(release.get("title", "")), _quality_name(release)))
            for release in matches
        ]
        same_lane = [
            (release, traits)
            for release, traits in same_lane
            if traits.resolution == local_traits.resolution and traits.source == local_traits.source
        ]
        blockers = [
            (release, traits)
            for release, traits in same_lane
            if release_is_equal_or_better(local_traits, traits)
        ]

        if blockers:
            best_release, best_traits = _best_release(blockers)
            decisions.append(
                {
                    "tracker": tracker,
                    "status": "blocked",
                    "reason": "Arr found an equal-or-better torrent result in the same lane.",
                    "matched_count": len(matches),
                    "best_release": _release_payload(best_release, best_traits),
                }
            )
            continue

        best_release_payload = None
        if same_lane:
            best_release, best_traits = _best_release(same_lane)
            best_release_payload = _release_payload(best_release, best_traits)
        decisions.append(
            {
                "tracker": tracker,
                "status": "candidate",
                "reason": "No equal-or-better torrent result found in the same lane.",
                "matched_count": len(matches),
                "best_release": best_release_payload,
            }
        )

    return decisions


def summarize_decisions(decisions: Sequence[Dict[str, Any]]) -> Tuple[str, str]:
    candidates = [str(item.get("tracker")) for item in decisions if item.get("status") == "candidate"]
    manual = [str(item.get("tracker")) for item in decisions if item.get("status") == "manual_review"]
    if candidates:
        return "candidate", f"Valid upload candidate on: {', '.join(candidates)}"
    if manual:
        return "manual_review", f"Arr comparison needs manual review for: {', '.join(manual)}"
    return "blocked", "UA passed, but Arr found equal-or-better torrent results."


def release_is_equal_or_better(local: ReleaseTraits, remote: ReleaseTraits) -> bool:
    if local.resolution != remote.resolution or local.source != remote.source:
        return False
    if local.season_pack and not remote.season_pack:
        return False
    if remote.season_pack and not local.season_pack:
        return True
    return remote.hdr_rank >= local.hdr_rank and remote.audio_channels >= local.audio_channels


def canonical_tracker(name: str) -> Optional[str]:
    cleaned = _compact(name)
    if not cleaned:
        return None
    for canonical, aliases in TRACKER_ALIASES.items():
        if cleaned == _compact(canonical) or cleaned in {_compact(alias) for alias in aliases}:
            return canonical
    for canonical, aliases in TRACKER_ALIASES.items():
        for alias in aliases:
            compact_alias = _compact(alias)
            if len(compact_alias) >= 4 and compact_alias in cleaned:
                return canonical
    return None


async def _sonarr_releases(
    identity: MediaIdentity,
    local_traits: ReleaseTraits,
    cfg: AppConfig,
    secrets: SecretStore,
) -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]]]:
    api_key = secrets.get("sonarr_api_key")
    if not cfg.sonarr.url or not api_key:
        raise RuntimeError("Sonarr URL or API key is not configured")
    client = SonarrClient(cfg.sonarr.url, api_key, cfg.safety.arr_search_timeout_seconds)
    indexers = await client.list_indexers()
    series = _match_series(await client.list_series(), identity)
    if not series:
        raise RuntimeError("No matching Sonarr series found")
    series_id = int(series["id"])
    if identity.season is None:
        raise RuntimeError("No season number found for Sonarr comparison")
    if local_traits.season_pack:
        releases = await client.search_releases(series_id=series_id, season_number=identity.season)
        return releases, indexers

    episodes = await client.list_episodes(series_id, identity.season)
    episode = _match_episode(episodes, identity.episode)
    if not episode:
        raise RuntimeError("No matching Sonarr episode found")
    releases = await client.search_releases(episode_id=int(episode["id"]))
    return releases, indexers


async def _radarr_releases(
    identity: MediaIdentity,
    cfg: AppConfig,
    secrets: SecretStore,
) -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]]]:
    api_key = secrets.get("radarr_api_key")
    if not cfg.radarr.url or not api_key:
        raise RuntimeError("Radarr URL or API key is not configured")
    client = RadarrClient(cfg.radarr.url, api_key, cfg.safety.arr_search_timeout_seconds)
    indexers = await client.list_indexers()
    movie = _match_movie(await client.list_movies(), identity)
    if not movie:
        raise RuntimeError("No matching Radarr movie found")
    releases = await client.search_releases(int(movie["id"]))
    return releases, indexers


def _match_series(series: Iterable[Dict[str, Any]], identity: MediaIdentity) -> Optional[Dict[str, Any]]:
    for item in series:
        if identity.tvdb_id and int(item.get("tvdbId") or 0) == identity.tvdb_id:
            return item
    return _match_title_year(series, identity)


def _match_movie(movies: Iterable[Dict[str, Any]], identity: MediaIdentity) -> Optional[Dict[str, Any]]:
    for item in movies:
        if identity.tmdb_id and int(item.get("tmdbId") or 0) == identity.tmdb_id:
            return item
        if identity.imdb_id and str(item.get("imdbId") or "").lower() == identity.imdb_id.lower():
            return item
    return _match_title_year(movies, identity)


def _match_title_year(items: Iterable[Dict[str, Any]], identity: MediaIdentity) -> Optional[Dict[str, Any]]:
    wanted = _compact(identity.title)
    if not wanted:
        return None
    for item in items:
        titles = [str(item.get("title") or ""), str(item.get("sortTitle") or "")]
        if item.get("alternateTitles"):
            titles.extend(str(alt.get("title") or "") for alt in item["alternateTitles"] if isinstance(alt, dict))
        if identity.year and int(item.get("year") or 0) not in {0, identity.year}:
            continue
        if wanted in {_compact(title) for title in titles}:
            return item
    return None


def _match_episode(episodes: Iterable[Dict[str, Any]], episode_number: Optional[int]) -> Optional[Dict[str, Any]]:
    if episode_number is None:
        return None
    for episode in episodes:
        if int(episode.get("episodeNumber") or 0) == episode_number:
            return episode
    return None


def _extract_title_year(text: str, fallback: str) -> Tuple[str, Optional[int]]:
    match = re.search(r"Title:\s*([^\n\r]+)", text, flags=re.IGNORECASE)
    title = match.group(1).strip() if match else fallback
    year_match = re.search(r"\((\d{4})\)", title)
    year = int(year_match.group(1)) if year_match else None
    title = re.sub(r"\s*\(\d{4}\)\s*$", "", title).strip()
    return title, year


def _parse_resolution(text: str) -> str:
    match = re.search(r"\b(2160|1080|720|480)p\b", text)
    return f"{match.group(1)}p" if match else ""


def _parse_source(text: str) -> str:
    if "remux" in text:
        return "bluray_remux"
    if re.search(r"\b(?:web[ ._-]?dl|webdl|web[ ._-]?rip|webrip|web)\b", text):
        return "web"
    if re.search(r"\b(?:blu[ ._-]?ray|bluray|bdrip|brrip|uhd[ ._-]?bluray)\b", text):
        return "bluray_encode"
    return "other"


def _parse_hdr_rank(text: str) -> int:
    if re.search(r"\b(?:dv|dovi|dolby[ ._-]?vision|hdr10\+|hdr10plus)\b", text):
        return 3
    if re.search(r"\b(?:hdr|hdr10)\b", text):
        return 2
    return 0


def _parse_audio_channels(title: str) -> float:
    patterns = [
        r"(?:DDP?|EAC3|AC3|AAC|DTS(?:[ ._-]?HD)?(?:[ ._-]?MA)?|TRUEHD|FLAC|PCM|OPUS|MP3|ATMOS)[ ._-]*(\d)[ ._-]?([01])",
        r"(?:^|[ ._-])(\d)[.]([01])(?:[ ._-]|$)",
    ]
    for pattern in patterns:
        matches = re.findall(pattern, title, flags=re.IGNORECASE)
        values = [float(f"{major}.{minor}") for major, minor in matches if major in {"1", "2", "5", "6", "7"}]
        if values:
            return max(values)
    return 0.0


def _parse_season_episode(title: str) -> Tuple[Optional[int], Optional[int]]:
    match = re.search(r"\bS(\d{1,2})(?:E(\d{1,3}))?\b", title, flags=re.IGNORECASE)
    if not match:
        return None, None
    season = int(match.group(1))
    episode = int(match.group(2)) if match.group(2) else None
    return season, episode


def _quality_name(release: Dict[str, Any]) -> str:
    quality = release.get("quality")
    if not isinstance(quality, dict):
        return ""
    nested = quality.get("quality")
    if isinstance(nested, dict):
        return str(nested.get("name") or "")
    return str(quality.get("name") or "")


def _best_release(items: Sequence[Tuple[Dict[str, Any], ReleaseTraits]]) -> Tuple[Dict[str, Any], ReleaseTraits]:
    return max(items, key=lambda item: _release_score(item[0], item[1]))


def _release_score(release: Dict[str, Any], traits: ReleaseTraits) -> Tuple[int, int, float, int, int]:
    return (
        1 if traits.season_pack else 0,
        traits.hdr_rank,
        traits.audio_channels,
        int(release.get("seeders") or 0),
        int(release.get("size") or 0),
    )


def _release_payload(release: Dict[str, Any], traits: ReleaseTraits) -> Dict[str, Any]:
    return {
        "title": str(release.get("title") or ""),
        "indexer": str(release.get("indexer") or ""),
        "quality": _quality_name(release),
        "size": int(release.get("size") or 0),
        "seeders": release.get("seeders"),
        "rejections": [
            str(item.get("reason") if isinstance(item, dict) else item)
            for item in release.get("rejections", [])
            if str(item).strip()
        ],
        "traits": _traits_payload(traits),
    }


def _manual_decisions(trackers: Sequence[str], reason: str) -> List[Dict[str, Any]]:
    return [
        {
            "tracker": tracker,
            "status": "manual_review",
            "reason": reason,
            "matched_count": 0,
            "best_release": None,
        }
        for tracker in trackers
    ]


def _traits_payload(traits: ReleaseTraits) -> Dict[str, Any]:
    payload = asdict(traits)
    payload["source_label"] = traits.source_label
    payload["hdr_label"] = traits.hdr_label
    return payload


def _media_payload(identity: MediaIdentity) -> Dict[str, Any]:
    return asdict(identity)


def _compact(value: str) -> str:
    return re.sub(r"[^a-z0-9]+", "", value.lower())
