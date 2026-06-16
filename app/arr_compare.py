from __future__ import annotations

import re
import time
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from typing import Any, Awaitable, Callable, Dict, Iterable, List, Mapping, Optional, Sequence, Tuple

import httpx

from app.clients import RadarrClient, SonarrClient
from app.config import AppConfig, SecretStore
from app.media_identity import (
    ReleaseTraits,
    parse_release_traits as _shared_parse_release_traits,
    release_is_equal_or_better as _shared_release_is_equal_or_better,
    release_score as _shared_release_score,
    same_release_lane as _shared_same_release_lane,
    traits_payload as _shared_traits_payload,
)
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


class ArrMetadataCache:
    def __init__(self) -> None:
        self._entries: Dict[Tuple[str, ...], Tuple[float, List[Dict[str, Any]]]] = {}

    async def get(
        self,
        key: Tuple[str, ...],
        ttl_seconds: int,
        loader: Callable[[], Awaitable[List[Dict[str, Any]]]],
    ) -> List[Dict[str, Any]]:
        if ttl_seconds <= 0:
            return await loader()
        now = time.monotonic()
        cached = self._entries.get(key)
        if cached and now - cached[0] < ttl_seconds:
            return [dict(row) for row in cached[1]]
        rows = await loader()
        self._entries[key] = (now, [dict(row) for row in rows if isinstance(row, dict)])
        return [dict(row) for row in rows if isinstance(row, dict)]


async def compare_item_with_arr(
    *,
    item_name: str,
    ua_log: str,
    passed_trackers: Sequence[str],
    cfg: AppConfig,
    secrets: SecretStore,
    local_traits: Optional[ReleaseTraits] = None,
    metadata_cache: Optional[ArrMetadataCache] = None,
) -> Dict[str, Any]:
    local_traits = local_traits or parse_release_traits(item_name)
    identity = parse_media_identity(ua_log, item_name, local_traits=local_traits)
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
            releases, indexers = await _sonarr_releases(identity, local_traits, cfg, secrets, metadata_cache)
        elif identity.kind == "radarr":
            releases, indexers = await _radarr_releases(identity, cfg, secrets, metadata_cache)
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


def parse_media_identity(log: str, item_name: str, local_traits: Optional[ReleaseTraits] = None) -> MediaIdentity:
    text = normalize_ua_log(log)
    title, year = _extract_title_year(text, item_name)
    tmdb_match = re.search(r"themoviedb\.org/(tv|movie)/(\d+)", text, flags=re.IGNORECASE)
    tmdb_kind = tmdb_match.group(1).lower() if tmdb_match else ""
    tmdb_id = int(tmdb_match.group(2)) if tmdb_match else None
    tvdb_match = re.search(r"(?:TVDB:|thetvdb\.com).*?(?:id=|series/)?(\d{3,})", text, flags=re.IGNORECASE)
    imdb_match = re.search(r"imdb\.com/title/(tt\d+)", text, flags=re.IGNORECASE)
    category_match = re.search(r"Category:\s*([^\n\r]+)", text, flags=re.IGNORECASE)
    category = category_match.group(1).strip().lower() if category_match else ""
    traits = local_traits or parse_release_traits(item_name)

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
    return _shared_parse_release_traits(title, quality_name)


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
                    "same_lane_count": 0,
                    "results": [],
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
                    "same_lane_count": 0,
                    "results": [],
                    "best_release": None,
                }
            )
            continue

        matches = [
            release
            for release in torrent_releases
            if canonical_tracker(str(release.get("indexer", ""))) == canon
        ]
        parsed_matches = [
            (release, parse_release_traits(str(release.get("title", "")), _quality_name(release)))
            for release in matches
        ]
        scoped_matches = [
            (release, traits)
            for release, traits in parsed_matches
            if _same_release_scope(local_traits, traits)
        ]
        result_payloads = [_release_payload(release, traits) for release, traits in scoped_matches]
        same_lane = [
            (release, traits)
            for release, traits in scoped_matches
            if _same_release_lane(local_traits, traits)
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
                    "same_lane_count": len(same_lane),
                    "results": result_payloads,
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
                "same_lane_count": len(same_lane),
                "results": result_payloads,
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
    return "skipped", "UA passed, but Arr found equal-or-better torrent results."


def release_is_equal_or_better(local: ReleaseTraits, remote: ReleaseTraits) -> bool:
    return _shared_release_is_equal_or_better(local, remote)


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
    metadata_cache: Optional[ArrMetadataCache] = None,
) -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]]]:
    api_key = secrets.get("sonarr_api_key")
    if not cfg.sonarr.url or not api_key:
        raise RuntimeError("Sonarr URL or API key is not configured")
    client = SonarrClient(cfg.sonarr.url, api_key, cfg.safety.arr_search_timeout_seconds)
    cache_ttl = _arr_metadata_cache_ttl(cfg)
    cache_prefix = ("sonarr", cfg.sonarr.url)
    indexers = await _cached_arr_metadata(metadata_cache, (*cache_prefix, "indexers"), cache_ttl, client.list_indexers)
    series_rows = await _cached_arr_metadata(metadata_cache, (*cache_prefix, "series"), cache_ttl, client.list_series)
    series = _match_series(series_rows, identity)
    if not series:
        raise RuntimeError("No matching Sonarr series found")
    series_id = int(series["id"])
    if identity.season is None:
        raise RuntimeError("No season number found for Sonarr comparison")
    episodes = await _cached_arr_metadata(
        metadata_cache,
        (*cache_prefix, "episodes", str(series_id), str(identity.season)),
        cache_ttl,
        lambda: client.list_episodes(series_id, identity.season),
    )
    if local_traits.season_pack:
        if not _season_has_started(episodes):
            raise RuntimeError("Sonarr season has not started airing yet; review pre-release uploads manually")
        releases = await client.search_releases(series_id=series_id, season_number=identity.season)
        return releases, indexers

    if _season_appears_fully_released(episodes, identity.season):
        raise RuntimeError("Sonarr season appears fully released; review for a season pack instead of an episode upload")
    episode = _match_episode(episodes, identity.episode)
    if not episode:
        raise RuntimeError("No matching Sonarr episode found")
    if not _episode_has_released(episode):
        raise RuntimeError("Sonarr episode has not aired yet; review pre-release uploads manually")
    releases = await client.search_releases(episode_id=int(episode["id"]))
    return releases, indexers


async def _radarr_releases(
    identity: MediaIdentity,
    cfg: AppConfig,
    secrets: SecretStore,
    metadata_cache: Optional[ArrMetadataCache] = None,
) -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]]]:
    api_key = secrets.get("radarr_api_key")
    if not cfg.radarr.url or not api_key:
        raise RuntimeError("Radarr URL or API key is not configured")
    client = RadarrClient(cfg.radarr.url, api_key, cfg.safety.arr_search_timeout_seconds)
    cache_ttl = _arr_metadata_cache_ttl(cfg)
    cache_prefix = ("radarr", cfg.radarr.url)
    indexers = await _cached_arr_metadata(metadata_cache, (*cache_prefix, "indexers"), cache_ttl, client.list_indexers)
    movie_rows = await _cached_arr_metadata(metadata_cache, (*cache_prefix, "movies"), cache_ttl, client.list_movies)
    movie = _match_movie(movie_rows, identity)
    if not movie:
        raise RuntimeError("No matching Radarr movie found")
    if not _movie_has_released(movie):
        raise RuntimeError("Radarr movie has not released yet; review pre-release uploads manually")
    releases = await client.search_releases(int(movie["id"]))
    return releases, indexers


def _arr_metadata_cache_ttl(cfg: AppConfig) -> int:
    try:
        return max(0, int(cfg.safety.arr_metadata_cache_seconds or 0))
    except (AttributeError, TypeError, ValueError):
        return 0


async def _cached_arr_metadata(
    metadata_cache: Optional[ArrMetadataCache],
    key: Tuple[str, ...],
    ttl_seconds: int,
    loader: Callable[[], Awaitable[List[Dict[str, Any]]]],
) -> List[Dict[str, Any]]:
    if metadata_cache is None:
        return await loader()
    return await metadata_cache.get(key, ttl_seconds, loader)


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


def _season_appears_fully_released(episodes: Sequence[Dict[str, Any]], season_number: Optional[int]) -> bool:
    if season_number is None:
        return False
    season_episodes = [
        episode
        for episode in episodes
        if int(episode.get("seasonNumber") or season_number) == season_number
        and int(episode.get("episodeNumber") or 0) > 0
    ]
    if len(season_episodes) < 2:
        return False
    monitored = [episode for episode in season_episodes if episode.get("monitored", True)]
    candidates = monitored or season_episodes
    return bool(candidates) and all(_episode_has_released(episode) for episode in candidates)


def _episode_has_released(episode: Mapping[str, Any]) -> bool:
    if episode.get("hasFile"):
        return True
    air_date = str(episode.get("airDateUtc") or episode.get("airDate") or "").strip()
    if not air_date:
        return False
    released_at = _parse_arr_datetime(air_date)
    if released_at is None:
        return False
    return released_at <= datetime.now(timezone.utc)


def _season_has_started(episodes: Sequence[Mapping[str, Any]]) -> bool:
    return any(_episode_has_released(episode) for episode in episodes)


def _movie_has_released(movie: Mapping[str, Any]) -> bool:
    if movie.get("hasFile"):
        return True
    status_value = str(movie.get("status") or "").strip().lower()
    if status_value == "released":
        return True
    date_fields = (
        "digitalRelease",
        "physicalRelease",
        "inCinemas",
        "premiereDate",
        "releaseDate",
    )
    dates = [_parse_arr_datetime(str(movie.get(field) or "").strip()) for field in date_fields]
    released_dates = [date for date in dates if date is not None]
    return bool(released_dates) and min(released_dates) <= datetime.now(timezone.utc)


def _parse_arr_datetime(value: str) -> Optional[datetime]:
    if not value:
        return None
    if value.endswith("Z"):
        value = value[:-1] + "+00:00"
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError:
        try:
            parsed = datetime.strptime(value, "%Y-%m-%d")
        except ValueError:
            return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed


def _extract_title_year(text: str, fallback: str) -> Tuple[str, Optional[int]]:
    match = re.search(r"Title:\s*([^\n\r]+)", text, flags=re.IGNORECASE)
    title = match.group(1).strip() if match else fallback
    year_match = re.search(r"\((\d{4})\)", title)
    year = int(year_match.group(1)) if year_match else None
    title = re.sub(r"\s*\(\d{4}\)\s*$", "", title).strip()
    return title, year


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


def _release_score(release: Dict[str, Any], traits: ReleaseTraits) -> Tuple[int, int, int, int, float, int, int]:
    return _shared_release_score(release, traits)


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
            "same_lane_count": 0,
            "results": [],
            "best_release": None,
        }
        for tracker in trackers
    ]


def _traits_payload(traits: ReleaseTraits) -> Dict[str, Any]:
    return _shared_traits_payload(traits)


def _same_release_lane(local: ReleaseTraits, remote: ReleaseTraits) -> bool:
    return _shared_same_release_lane(local, remote)


def _same_release_scope(local: ReleaseTraits, remote: ReleaseTraits) -> bool:
    if local.season is None:
        return True
    if remote.season != local.season:
        return False
    if local.season_pack:
        return remote.season_pack
    return remote.episode == local.episode and not remote.season_pack


def _media_payload(identity: MediaIdentity) -> Dict[str, Any]:
    return asdict(identity)


def _compact(value: str) -> str:
    return re.sub(r"[^a-z0-9]+", "", value.lower())
