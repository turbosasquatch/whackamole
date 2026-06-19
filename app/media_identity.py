from __future__ import annotations

import json
import re
from dataclasses import asdict, dataclass
from pathlib import PurePosixPath
from typing import Any, Dict, List, Mapping, Optional, Sequence, Tuple


SOURCE_LABELS = {
    "web": "WEB",
    "bluray_remux": "BluRay Remux",
    "bluray_encode": "BluRay Encode",
    "hdtv": "HDTV",
    "dvd": "DVD",
    "cam": "CAM/TS",
    "other": "Other",
}

HDR_LABELS = {
    4: "DV/HDR fallback",
    3: "DV only",
    2: "HDR10+",
    1: "HDR",
    0: "SDR",
}

AUDIO_FORMAT_RANKS = {
    "Opus": 1,
    "MP3": 2,
    "Vorbis": 2,
    "MP2": 2,
    "DD": 3,
    "AAC": 4,
    "HE-AAC": 4,
    "DTS": 5,
    "DTS-ES": 6,
    "DD+": 7,
    "DTS-HD HRA": 8,
    "PCM": 9,
    "FLAC": 10,
    "DTS-HD MA": 11,
    "TrueHD": 12,
    "DD+ Atmos": 13,
    "Atmos": 14,
    "DTS:X": 15,
    "TrueHD Atmos": 16,
}

CODEC_RANKS = {
    "": 0,
    "MPEG-2": 1,
    "MPEG-4 Visual": 2,
    "XviD": 2,
    "DivX": 2,
    "VC-1": 3,
    "AVC": 4,
    "HEVC": 5,
    "VP9": 5,
    "AV1": 6,
    "VVC": 7,
}

PROVIDER_ALIASES = {
    "NF": "Netflix",
    "AMZN": "Amazon",
    "DSNP": "Disney+",
    "ATV": "Apple TV+",
    "ATVP": "Apple TV+",
    "HMAX": "Max",
    "HULU": "Hulu",
    "PMTP": "Paramount+",
    "CR": "Crunchyroll",
    "PCOK": "Peacock",
    "IP": "iPlayer",
    "MA": "Movies Anywhere",
}

MOVIE_VERSION_PATTERNS: Sequence[Tuple[str, str]] = (
    ("Director's Cut", r"\bdirectors?[ ._-]?cut\b"),
    ("Final Cut", r"\bfinal[ ._-]?cut\b"),
    ("Anniversary Edition", r"\banniversary\b"),
    ("Collector's Edition", r"\bcollectors?\b"),
    ("Criterion Collection", r"\bcriterion\b"),
    ("4K Remaster", r"\b4k[ ._-]?remaster(?:ed)?\b"),
    ("Remastered", r"\bremaster(?:ed)?\b"),
    ("Restored", r"\brestore(?:d)?\b"),
    ("IMAX Enhanced", r"\bimax[ ._-]?enhanced\b"),
    ("Special Edition", r"\bspecial[ ._-]?edition\b"),
    ("Extended", r"\bextended\b"),
    ("Unrated", r"\bunrated\b"),
    ("Uncut", r"\buncut\b"),
    ("Theatrical Cut", r"\btheatrical(?:[ ._-]?cut)?\b"),
    ("Open Matte", r"\bopen[ ._-]?matte\b"),
    ("Hybrid", r"\bhybrid\b"),
    ("IMAX", r"\bimax\b"),
    ("AI Upscale", r"\bai[ ._-]?upscale\b"),
    ("Upscale", r"\bupscale\b"),
)

BITRATE_VIDEO_RULES: Sequence[Tuple[str, str, str, Optional[str], float, float]] = (
    ("1080p", "web", "AVC", None, 4.0, 16.0),
    ("1080p", "web", "HEVC", None, 2.5, 12.0),
    ("1080p", "bluray_encode", "AVC", None, 6.0, 22.0),
    ("2160p", "web", "HEVC", None, 10.0, 35.0),
    ("2160p", "bluray_encode", "", None, 18.0, 70.0),
    ("2160p", "bluray_remux", "", "remux", 35.0, 120.0),
)

BITRATE_AUDIO_RULES: Sequence[Tuple[str, Optional[float], bool, float, float]] = (
    ("AAC", 2.0, False, 96.0, 320.0),
    ("DD", 5.1, False, 384.0, 640.0),
    ("DD+", 5.1, False, 384.0, 1024.0),
    ("DD+", None, True, 640.0, 1536.0),
    ("TrueHD", None, False, 2000.0, 8000.0),
    ("TrueHD Atmos", None, True, 2000.0, 8000.0),
    ("DTS-HD MA", None, False, 2000.0, 8000.0),
)

LANGUAGE_ALIASES = {
    "ar": "arabic",
    "ara": "arabic",
    "arabic": "arabic",
    "cmn": "mandarin",
    "mandarin": "mandarin",
    "cs": "czech",
    "ces": "czech",
    "cze": "czech",
    "czech": "czech",
    "da": "danish",
    "dan": "danish",
    "danish": "danish",
    "de": "german",
    "deu": "german",
    "ger": "german",
    "german": "german",
    "el": "greek",
    "ell": "greek",
    "gre": "greek",
    "greek": "greek",
    "en": "english",
    "eng": "english",
    "english": "english",
    "es": "spanish",
    "spa": "spanish",
    "spanish": "spanish",
    "fa": "persian",
    "fas": "persian",
    "farsi": "persian",
    "per": "persian",
    "persian": "persian",
    "fi": "finnish",
    "fin": "finnish",
    "finnish": "finnish",
    "fr": "french",
    "fra": "french",
    "fre": "french",
    "french": "french",
    "he": "hebrew",
    "heb": "hebrew",
    "hebrew": "hebrew",
    "hi": "hindi",
    "hin": "hindi",
    "hindi": "hindi",
    "hu": "hungarian",
    "hun": "hungarian",
    "hungarian": "hungarian",
    "id": "indonesian",
    "ind": "indonesian",
    "indonesian": "indonesian",
    "it": "italian",
    "ita": "italian",
    "italian": "italian",
    "ja": "japanese",
    "jpn": "japanese",
    "japanese": "japanese",
    "ko": "korean",
    "kor": "korean",
    "korean": "korean",
    "mul": "multi",
    "multi": "multi",
    "multi-language": "multi",
    "multiple": "multi",
    "multiple languages": "multi",
    "nl": "dutch",
    "nld": "dutch",
    "dut": "dutch",
    "dutch": "dutch",
    "no": "norwegian",
    "nor": "norwegian",
    "norwegian": "norwegian",
    "pl": "polish",
    "pol": "polish",
    "polish": "polish",
    "pt": "portuguese",
    "por": "portuguese",
    "portuguese": "portuguese",
    "ro": "romanian",
    "ron": "romanian",
    "rum": "romanian",
    "romanian": "romanian",
    "ru": "russian",
    "rus": "russian",
    "russian": "russian",
    "sv": "swedish",
    "swe": "swedish",
    "swedish": "swedish",
    "th": "thai",
    "tha": "thai",
    "thai": "thai",
    "tr": "turkish",
    "tur": "turkish",
    "turkish": "turkish",
    "uk": "ukrainian",
    "ukr": "ukrainian",
    "ukrainian": "ukrainian",
    "vi": "vietnamese",
    "vie": "vietnamese",
    "vietnamese": "vietnamese",
    "yue": "cantonese",
    "cantonese": "cantonese",
    "zh": "chinese",
    "chi": "chinese",
    "zho": "chinese",
    "chinese": "chinese",
}
LANGUAGE_LABELS = set(LANGUAGE_ALIASES.values())


@dataclass(frozen=True)
class ReleaseTraits:
    title: str
    resolution: str = ""
    scan_type: str = ""
    source: str = "other"
    source_tag: str = ""
    source_provider: str = ""
    rip_type: str = ""
    hdr_rank: int = 0
    hdr_formats: Tuple[str, ...] = ()
    dv_profile: str = ""
    audio_format: str = ""
    audio_format_rank: int = 0
    audio_channels: float = 0.0
    audio_objects: Tuple[str, ...] = ()
    codec: str = ""
    bit_depth: str = ""
    chroma: str = ""
    movie_versions: Tuple[str, ...] = ()
    release_group: str = ""
    container: str = ""
    languages: Tuple[str, ...] = ()
    subtitle_tags: Tuple[str, ...] = ()
    tags: Tuple[str, ...] = ()
    custom_formats: Tuple[str, ...] = ()
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


def parse_release_traits(title: str, quality_name: str = "") -> ReleaseTraits:
    text = f"{title} {quality_name}".replace("_", ".")
    normalized = _normalized_text(text)
    season, episode = _parse_season_episode(text)
    resolution, scan_type = _parse_resolution(normalized)
    audio_format, audio_rank = _parse_audio_format(normalized)
    audio_objects = _parse_audio_objects(normalized, audio_format)
    hdr_rank, hdr_formats, dv_profile = _parse_hdr(normalized)
    source, source_tag, provider, rip_type = _parse_source(normalized)
    codec = _parse_codec(normalized)
    movie_versions = _parse_movie_versions(normalized)
    tags = _dedupe(
        [
            value
            for value in (
                resolution,
                scan_type,
                source_tag,
                provider,
                rip_type,
                audio_format,
                _format_channels(_parse_audio_channels(text)),
                *audio_objects,
                *hdr_formats,
                dv_profile,
                codec,
                _parse_bit_depth(normalized),
                _parse_chroma(normalized),
                *_parse_languages(normalized),
                *_parse_subtitle_tags(normalized),
                *movie_versions,
                _parse_container(normalized),
            )
            if value
        ]
    )
    custom_formats = _custom_formats_from_tags(tags, source, codec, audio_format, hdr_formats, movie_versions)
    return ReleaseTraits(
        title=title,
        resolution=resolution,
        scan_type=scan_type,
        source=source,
        source_tag=source_tag,
        source_provider=provider,
        rip_type=rip_type,
        hdr_rank=hdr_rank,
        hdr_formats=tuple(hdr_formats),
        dv_profile=dv_profile,
        audio_format=audio_format,
        audio_format_rank=audio_rank,
        audio_channels=_parse_audio_channels(text),
        audio_objects=tuple(audio_objects),
        codec=codec,
        bit_depth=_parse_bit_depth(normalized),
        chroma=_parse_chroma(normalized),
        movie_versions=tuple(movie_versions),
        release_group=extract_release_group(title),
        container=_parse_container(normalized),
        languages=tuple(_parse_languages(normalized)),
        subtitle_tags=tuple(_parse_subtitle_tags(normalized)),
        tags=tuple(tags),
        custom_formats=tuple(custom_formats),
        season=season,
        episode=episode,
        season_pack=season is not None and episode is None,
    )


def traits_payload(traits: ReleaseTraits) -> Dict[str, Any]:
    payload = asdict(traits)
    payload["movie_versions"] = list(traits.movie_versions)
    payload["hdr_formats"] = list(traits.hdr_formats)
    payload["audio_objects"] = list(traits.audio_objects)
    payload["languages"] = list(traits.languages)
    payload["subtitle_tags"] = list(traits.subtitle_tags)
    payload["tags"] = list(traits.tags)
    payload["custom_formats"] = list(traits.custom_formats)
    payload["source_label"] = traits.source_label
    payload["hdr_label"] = traits.hdr_label
    return payload


def traits_from_payload(value: Mapping[str, Any]) -> ReleaseTraits:
    payload = value if isinstance(value, Mapping) else {}
    return ReleaseTraits(
        title=str(payload.get("title") or ""),
        resolution=str(payload.get("resolution") or ""),
        scan_type=str(payload.get("scan_type") or ""),
        source=str(payload.get("source") or "other"),
        source_tag=str(payload.get("source_tag") or ""),
        source_provider=str(payload.get("source_provider") or ""),
        rip_type=str(payload.get("rip_type") or ""),
        hdr_rank=_int_payload_value(payload.get("hdr_rank")),
        hdr_formats=tuple(_string_sequence(payload.get("hdr_formats"))),
        dv_profile=str(payload.get("dv_profile") or ""),
        audio_format=str(payload.get("audio_format") or ""),
        audio_format_rank=_int_payload_value(payload.get("audio_format_rank")),
        audio_channels=_float_payload_value(payload.get("audio_channels")),
        audio_objects=tuple(_string_sequence(payload.get("audio_objects"))),
        codec=str(payload.get("codec") or ""),
        bit_depth=str(payload.get("bit_depth") or ""),
        chroma=str(payload.get("chroma") or ""),
        movie_versions=tuple(_string_sequence(payload.get("movie_versions"))),
        release_group=str(payload.get("release_group") or ""),
        container=str(payload.get("container") or ""),
        languages=tuple(_string_sequence(payload.get("languages"))),
        subtitle_tags=tuple(_string_sequence(payload.get("subtitle_tags"))),
        tags=tuple(_string_sequence(payload.get("tags"))),
        custom_formats=tuple(_string_sequence(payload.get("custom_formats"))),
        season=_optional_int_payload_value(payload.get("season")),
        episode=_optional_int_payload_value(payload.get("episode")),
        season_pack=_bool_payload_value(payload.get("season_pack")),
    )


def normalize_language_label(value: Any) -> str:
    text = re.sub(r"\s*\([^)]*\)", "", str(value or "").strip().lower())
    if not text:
        return ""
    primary = text.split("-", 1)[0].strip()
    return LANGUAGE_ALIASES.get(primary, LANGUAGE_ALIASES.get(text, primary or text))


def language_is_confident(value: Any) -> bool:
    text = re.sub(r"\s*\([^)]*\)", "", str(value or "").strip().lower())
    if not text:
        return False
    primary = text.split("-", 1)[0].strip()
    normalized = normalize_language_label(text)
    return primary in LANGUAGE_ALIASES or text in LANGUAGE_ALIASES or normalized in LANGUAGE_LABELS


def media_display_fields_from_files(
    release_title: str,
    media_files: Sequence[Mapping[str, Any]],
    *,
    suppressed_labels: Sequence[str] = (),
) -> Dict[str, Any]:
    title_traits = parse_release_traits(release_title)
    suppressed = {str(label) for label in suppressed_labels if str(label)}
    confirmed_tags = _media_file_labels(media_files, key="tags", suppressed=suppressed)
    custom_formats = _media_file_labels(media_files, key="custom_formats", suppressed=suppressed)
    media_tags = _dedupe([*confirmed_tags, *custom_formats])
    title_tags = _title_display_tags(title_traits)
    return {
        "title_tags": title_tags,
        "media_tags": media_tags,
        "title_tag_matches": _title_tag_matches(title_traits, title_tags, media_tags),
        "confirmed_tags": confirmed_tags,
        "custom_formats": custom_formats,
    }


def ensure_media_display_fields(media: Mapping[str, Any], release_title: str = "") -> Dict[str, Any]:
    result = dict(media) if isinstance(media, Mapping) else {}
    if not result:
        return result
    if result.get("title_tags") and result.get("media_tags") and result.get("title_tag_matches"):
        return result
    title = str(
        result.get("release_title")
        or result.get("torrent_root")
        or (result.get("local_traits") if isinstance(result.get("local_traits"), Mapping) else {}).get("title")
        or release_title
        or ""
    )
    files = list(result.get("mediainfo_files") if isinstance(result.get("mediainfo_files"), list) else [])
    supplemental = result.get("supplemental_mediainfo_files")
    if isinstance(supplemental, list):
        files.extend(item for item in supplemental if isinstance(item, Mapping))
    display = media_display_fields_from_files(
        title,
        [item for item in files if isinstance(item, Mapping)],
        suppressed_labels=_suppressed_display_labels_from_issues(
            result.get("issues") if isinstance(result.get("issues"), list) else []
        ),
    )
    result.update(display)
    return result


def analyze_media_payloads(
    *,
    release_title: str,
    media_files: Sequence[Mapping[str, Any]],
    mediainfo_payloads: Sequence[Mapping[str, Any]],
) -> Dict[str, Any]:
    title_traits = parse_release_traits(release_title)
    analyzed_files = []
    issues: List[Dict[str, Any]] = []
    for payload in mediainfo_payloads:
        file_result = media_file_payload(payload)
        analyzed_files.append(file_result)
        if _is_sample_video_file(str(file_result.get("name") or "")):
            continue
        issues.extend(_validate_media_file(title_traits, file_result))

    if not media_files:
        issues.append(_issue("ERROR", "no_video_files", "QUI did not report any video files for this torrent."))
    if media_files and not mediainfo_payloads:
        issues.append(_issue("ERROR", "mediainfo_missing", "QUI did not return MediaInfo for any video files."))

    if len(mediainfo_payloads) < len(media_files):
        issues.append(
            _issue(
                "INFO",
                "mediainfo_truncated",
                f"Checked MediaInfo for {len(mediainfo_payloads)} of {len(media_files)} video files.",
            )
        )

    severities = {str(item.get("severity") or "") for item in issues}
    status = "manual_review" if "ERROR" in severities else "passed"
    verdict = "media_error" if status == "manual_review" else ("media_warning" if "WARNING" in severities else "media_confirmed")
    reason = _media_reason(issues)
    display_fields = media_display_fields_from_files(release_title, analyzed_files)
    return {
        "version": 1,
        "source": "mediainfo",
        "status": status,
        "media_status": "error" if "ERROR" in severities else ("warning" if "WARNING" in severities else "confirmed"),
        "verdict": verdict,
        "reason": reason,
        "release_title": release_title,
        "release_group": title_traits.release_group,
        "local_traits": traits_payload(title_traits),
        **display_fields,
        "issues": issues,
        "mediainfo_files": analyzed_files,
        "video_files": [
            {
                "index": int(item.get("index") or 0),
                "name": str(item.get("name") or item.get("basename") or ""),
                "size": int(item.get("size") or 0),
            }
            for item in media_files
        ],
        "flags": _issues_as_flags(issues),
    }


def media_file_payload(payload: Mapping[str, Any]) -> Dict[str, Any]:
    tracks = mediainfo_tracks(payload)
    video = _first_track(tracks, "video")
    audios = _tracks_by_type(tracks, "audio")
    texts = _tracks_by_type(tracks, "text")
    subtitle_count = _subtitle_count(tracks)
    default_audio = _default_track(audios) or (audios[0] if audios else {})
    traits = traits_from_mediainfo(payload)
    media = _mapping_value(payload, "media")
    media_ref = _mapping_value(media, "@ref") if isinstance(media, Mapping) else ""
    return {
        "index": int(payload.get("fileIndex") or payload.get("index") or 0),
        "name": str(payload.get("relativePath") or payload.get("path") or media_ref or ""),
        "traits": traits_payload(traits),
        "tags": list(traits.tags),
        "custom_formats": list(traits.custom_formats),
        "video": _track_payload(video),
        "audio": [_track_payload(track) for track in audios],
        "text": [_track_payload(track) for track in texts],
        "subtitle_count": subtitle_count,
        "default_audio": _track_payload(default_audio),
    }


def traits_from_mediainfo(payload: Mapping[str, Any]) -> ReleaseTraits:
    tracks = mediainfo_tracks(payload)
    video = _first_track(tracks, "video")
    audios = _tracks_by_type(tracks, "audio")
    audio = _default_track(audios) or (audios[0] if audios else {})
    text = " ".join(
        str(value)
        for track in (video, audio)
        for value in track.values()
        if isinstance(value, (str, int, float))
    )
    fallback = parse_release_traits(text)
    width = _number_from_value(_track_value(video, "Width", "width"))
    height = _number_from_value(_track_value(video, "Height", "height"))
    scan = _track_text(video, "ScanType", "scanType").lower()
    resolution = _resolution_from_dimensions(width, height, scan)
    codec = _codec_from_mediainfo(video, fallback.codec)
    audio_format = _audio_format_from_mediainfo(audio, fallback.audio_format)
    channels = _mediainfo_channels(audio)
    audio_objects = _audio_objects_from_tracks(audios)
    hdr_rank, hdr_formats, dv_profile = _hdr_from_mediainfo(video, fallback)
    bit_depth = _track_text(video, "BitDepth", "bitDepth") or fallback.bit_depth
    chroma = _track_text(video, "ChromaSubsampling", "chromaSubsampling") or fallback.chroma
    subtitle_count = _subtitle_count(tracks)
    tags = _dedupe(
        [
            value
            for value in (
                resolution,
                "interlaced" if scan.startswith("inter") else ("progressive" if scan.startswith("prog") else ""),
                codec,
                audio_format,
                _format_channels(channels),
                *audio_objects,
                *hdr_formats,
                dv_profile,
                bit_depth,
                chroma,
                *_track_languages(audios),
                *_subtitle_tags_from_tracks(_tracks_by_type(tracks, "text"), subtitle_count),
            )
            if value
        ]
    )
    return ReleaseTraits(
        title=str(payload.get("relativePath") or payload.get("path") or ""),
        resolution=resolution,
        scan_type="interlaced" if scan.startswith("inter") else ("progressive" if scan.startswith("prog") else ""),
        hdr_rank=hdr_rank,
        hdr_formats=tuple(hdr_formats),
        dv_profile=dv_profile,
        audio_format=audio_format,
        audio_format_rank=AUDIO_FORMAT_RANKS.get(audio_format, 0),
        audio_channels=channels,
        audio_objects=tuple(audio_objects),
        codec=codec,
        bit_depth=bit_depth,
        chroma=chroma,
        languages=tuple(_track_languages(audios)),
        subtitle_tags=tuple(_subtitle_tags_from_tracks(_tracks_by_type(tracks, "text"), subtitle_count)),
        tags=tuple(tags),
        custom_formats=tuple(_custom_formats_from_tags(tags, "", codec, audio_format, hdr_formats, ())),
    )


def mediainfo_tracks(payload: Mapping[str, Any]) -> List[Mapping[str, Any]]:
    return _mediainfo_tracks(payload, depth=0)


def _mediainfo_tracks(payload: Mapping[str, Any], *, depth: int) -> List[Mapping[str, Any]]:
    if depth > 6:
        return []
    raw = _mapping_value(payload, "rawJSON")
    if isinstance(raw, str) and raw.strip():
        try:
            decoded = json.loads(raw)
        except json.JSONDecodeError:
            decoded = {}
        nested = _mediainfo_tracks(decoded, depth=depth + 1) if isinstance(decoded, Mapping) else []
        if nested:
            return nested
    elif isinstance(raw, Mapping):
        nested = _mediainfo_tracks(raw, depth=depth + 1)
        if nested:
            return nested

    for key in ("streams", "track", "tracks"):
        tracks = _mapping_value(payload, key)
        if isinstance(tracks, list):
            normalised = _normalise_mediainfo_track_list(tracks)
            if normalised:
                return normalised
    media = _mapping_value(payload, "media")
    if isinstance(media, Mapping):
        for key in ("track", "tracks", "streams"):
            tracks = _mapping_value(media, key)
            if isinstance(tracks, list):
                normalised = _normalise_mediainfo_track_list(tracks)
                if normalised:
                    return normalised
    for key in ("data", "result", "response", "payload", "body", "mediainfo", "mediaInfo", "media_info"):
        nested_payload = _mapping_value(payload, key)
        if isinstance(nested_payload, Mapping):
            nested = _mediainfo_tracks(nested_payload, depth=depth + 1)
            if nested:
                return nested
    return []


def _normalise_mediainfo_track_list(tracks: Sequence[Any]) -> List[Mapping[str, Any]]:
    normalised: List[Mapping[str, Any]] = []
    for track in tracks:
        if not isinstance(track, Mapping):
            continue
        normalised_track = _normalise_mediainfo_track(track)
        if normalised_track:
            normalised.append(normalised_track)
    return normalised


def _normalise_mediainfo_track(track: Mapping[str, Any]) -> Mapping[str, Any]:
    normalised = dict(track)
    fields = _mapping_value(normalised, "fields")
    if fields is not None:
        normalised.pop("fields", None)
        normalised.pop("Fields", None)
        for name, value in _iter_mediainfo_fields(fields):
            if not name or value is None:
                continue
            normalised.setdefault(name, value)
            compact = _compact_mapping_key(name)
            alias = _MEDIAINFO_FIELD_ALIASES.get(compact)
            if alias:
                normalised.setdefault(alias, value)
            auto_key = re.sub(r"[^A-Za-z0-9]+", "_", name).strip("_")
            if auto_key:
                normalised.setdefault(auto_key, value)
    kind = _mapping_value(normalised, "@type", "type", "kind")
    if kind and not _mapping_value(normalised, "@type", "type"):
        normalised["@type"] = str(kind)
    return normalised


def _iter_mediainfo_fields(fields: Any) -> List[Tuple[str, Any]]:
    if isinstance(fields, Mapping):
        return [(str(key), value) for key, value in fields.items()]
    if not isinstance(fields, list):
        return []
    values: List[Tuple[str, Any]] = []
    for field in fields:
        if isinstance(field, Mapping):
            name = _mapping_value(field, "name", "key", "label")
            value = _mapping_value(field, "value", "text", "raw")
            values.append((str(name or ""), value))
        elif isinstance(field, (list, tuple)) and len(field) >= 2:
            values.append((str(field[0]), field[1]))
    return values


_MEDIAINFO_FIELD_ALIASES = {
    "commercialname": "Format_Commercial_IfAny",
    "hdrformat": "HDR_Format",
    "formatprofile": "Format_Profile",
    "formatinfo": "Format_Info",
    "codecid": "CodecID",
    "codecidinfo": "CodecID_Info",
    "bitrate": "BitRate",
    "bitratemode": "BitRate_Mode",
    "channels": "Channels",
    "channellayout": "ChannelLayout",
    "chromasubsampling": "ChromaSubsampling",
    "bitdepth": "BitDepth",
    "colorprimaries": "ColorPrimaries",
    "colourprimaries": "colour_primaries",
    "transfercharacteristics": "TransferCharacteristics",
    "matrixcoefficients": "MatrixCoefficients",
    "masteringdisplaycolorprimaries": "MasteringDisplay_ColorPrimaries",
    "masteringdisplaycolourprimaries": "MasteringDisplay_ColorPrimaries",
    "masteringdisplayluminance": "MasteringDisplay_Luminance",
    "maximumcontentlightlevel": "MaxCLL",
    "maximumframeaveragelightlevel": "MaxFALL",
}


def extract_release_group(value: str) -> str:
    name = PurePosixPath(str(value or "")).name
    name = re.sub(r"\.(?:mkv|mp4|m4v|avi|m2ts|ts|mov|wmv|nfo)$", "", name, flags=re.IGNORECASE)
    name = re.sub(r"[\s.)\]}]+$", "", name)
    match = re.search(r"-\s*([A-Za-z0-9][A-Za-z0-9._&+]{1,})$", name)
    if match and _looks_like_release_group(match.group(1)):
        return match.group(1)
    tail = re.split(r"[ ._\[\]()]+", name.strip())[-1]
    if _looks_like_release_group(tail):
        return tail
    return ""


def _looks_like_release_group(value: str) -> bool:
    cleaned = str(value or "").strip()
    if len(cleaned) < 2 or len(cleaned) > 20:
        return False
    if not re.search(r"[A-Za-z]", cleaned):
        return False
    lowered = cleaned.lower()
    parts = {part for part in re.split(r"[^a-z0-9]+", lowered) if part}
    if lowered in {
        "web",
        "webdl",
        "webrip",
        "hdtv",
        "bluray",
        "remux",
        "h264",
        "x264",
        "h265",
        "x265",
        "hevc",
        "avc",
        "hdr",
        "dv",
        "atmos",
    }:
        return False
    if parts & {"web", "dl", "rip", "webrip", "webdl", "h264", "h265", "x264", "x265", "hevc", "avc"}:
        return False
    if parts & {"dd", "ddp", "dts", "hd", "ma", "truehd", "atmos", "aac", "ac3", "eac3"}:
        return False
    if re.fullmatch(r"[hx]\d{3}", lowered):
        return False
    return True


def same_release_lane(local: ReleaseTraits, remote: ReleaseTraits) -> bool:
    return (
        _resolution_height(local.resolution) == _resolution_height(remote.resolution)
        and local.source == remote.source
        and _same_release_scope(local, remote)
        and _lane_movie_versions(local) == _lane_movie_versions(remote)
    )


def release_is_equal_or_better(local: ReleaseTraits, remote: ReleaseTraits) -> bool:
    if not same_release_lane(local, remote):
        return False
    if local.season_pack and not remote.season_pack:
        return False
    if remote.season_pack and not local.season_pack:
        return True
    return (
        _scan_rank(remote) >= _scan_rank(local)
        and _hdr_satisfies(local, remote)
        and remote.audio_format_rank >= local.audio_format_rank
        and remote.audio_channels >= local.audio_channels
        and CODEC_RANKS.get(remote.codec, 0) >= CODEC_RANKS.get(local.codec, 0)
    )


def release_score(release: Mapping[str, Any], traits: ReleaseTraits) -> Tuple[int, int, int, int, int, float, int, int]:
    return (
        1 if traits.season_pack else 0,
        _scan_rank(traits),
        _score_hdr_rank(traits),
        CODEC_RANKS.get(traits.codec, 0),
        traits.audio_format_rank,
        traits.audio_channels,
        int(release.get("seeders") or 0),
        int(release.get("size") or 0),
    )


def _lane_movie_versions(traits: ReleaseTraits) -> Tuple[str, ...]:
    return tuple(version for version in traits.movie_versions if version != "Hybrid")


def _same_release_scope(local: ReleaseTraits, remote: ReleaseTraits) -> bool:
    if local.season is None:
        return True
    if remote.season != local.season:
        return False
    if local.season_pack:
        return remote.season_pack
    return remote.episode == local.episode and not remote.season_pack


def _hdr_satisfies(local: ReleaseTraits, remote: ReleaseTraits) -> bool:
    if remote.hdr_rank >= local.hdr_rank:
        return True
    local_formats = set(local.hdr_formats)
    remote_formats = set(remote.hdr_formats)
    if not remote_formats and remote.hdr_rank == 0:
        return True
    if "HDR10+" in local_formats and "HDR10+" in remote_formats:
        return True
    if (
        "Dolby Vision" in local_formats
        and "HDR10" in local_formats
        and ("HDR10" in remote_formats or remote.hdr_rank == 1)
    ):
        return True
    return False


def _score_hdr_rank(traits: ReleaseTraits) -> int:
    if traits.hdr_rank == 0 and not traits.hdr_formats and _resolution_height_int(traits.resolution) >= 2160 and traits.source == "web":
        return 1
    return traits.hdr_rank


def _validate_media_file(title_traits: ReleaseTraits, file_result: Mapping[str, Any]) -> List[Dict[str, Any]]:
    traits = file_result.get("traits") if isinstance(file_result.get("traits"), Mapping) else {}
    video = file_result.get("video") if isinstance(file_result.get("video"), Mapping) else {}
    audios = file_result.get("audio") if isinstance(file_result.get("audio"), list) else []
    texts = file_result.get("text") if isinstance(file_result.get("text"), list) else []
    subtitle_count = int(file_result.get("subtitle_count") or len(texts) or 0)
    default_audio = file_result.get("default_audio") if isinstance(file_result.get("default_audio"), Mapping) else {}
    issues: List[Dict[str, Any]] = []

    if title_traits.resolution:
        media_resolution = str(traits.get("resolution") or "")
        if media_resolution and not _resolution_matches(title_traits.resolution, media_resolution, video):
            issues.append(
                _issue(
                    "ERROR",
                    "resolution_mismatch",
                    f"Name says {title_traits.resolution}, but MediaInfo reports {media_resolution}.",
                    file_result,
                )
            )

    if title_traits.codec and traits.get("codec") and title_traits.codec != traits.get("codec"):
        issues.append(
            _issue(
                "ERROR",
                "video_codec_mismatch",
                f"Name says {title_traits.codec}, but MediaInfo reports {traits.get('codec')}.",
                file_result,
            )
        )

    if title_traits.audio_format and title_traits.audio_format != "Atmos" and traits.get("audio_format"):
        expected = _audio_family(title_traits.audio_format)
        actual = _audio_family(str(traits.get("audio_format") or ""))
        if expected != actual:
            issues.append(
                _issue(
                    "ERROR",
                    "audio_codec_mismatch",
                    f"Name says {title_traits.audio_format}, but MediaInfo reports {traits.get('audio_format')}.",
                    file_result,
                )
            )

    if title_traits.audio_channels and traits.get("audio_channels"):
        if float(traits.get("audio_channels") or 0) != float(title_traits.audio_channels):
            issues.append(
                _issue(
                    "ERROR",
                    "audio_channels_mismatch",
                    f"Name says {_format_channels(title_traits.audio_channels)}, but MediaInfo reports {_format_channels(float(traits.get('audio_channels') or 0))}.",
                    file_result,
                )
            )

    media_objects = set(traits.get("audio_objects") or [])
    for audio_object in title_traits.audio_objects:
        if audio_object not in media_objects:
            issues.append(
                _issue(
                    "ERROR",
                    "audio_object_missing",
                    f"Name says {audio_object}, but MediaInfo has no matching object/JOC metadata.",
                    file_result,
                )
            )

    issues.extend(_validate_hdr(title_traits, traits, file_result))
    issues.extend(_policy_blocking_issues(title_traits, default_audio, file_result))
    issues.extend(_bitrate_warnings(title_traits, video, default_audio, file_result))
    issues.extend(_track_sanity_warnings(title_traits, audios, texts, subtitle_count, default_audio, traits, file_result))

    if not issues:
        issues.append(_issue("INFO", "media_confirmed", "MediaInfo traits match the release name.", file_result))
    return issues


def _policy_blocking_issues(
    title_traits: ReleaseTraits,
    default_audio: Mapping[str, Any],
    file_result: Mapping[str, Any],
) -> List[Dict[str, Any]]:
    issues: List[Dict[str, Any]] = []
    if (
        title_traits.resolution == "1080p"
        and title_traits.source in {"bluray_encode", "bluray_remux"}
        and str((file_result.get("traits") if isinstance(file_result.get("traits"), Mapping) else {}).get("audio_format") or title_traits.audio_format)
        == "DTS-HD MA"
    ):
        issues.append(
            _issue(
                "ERROR",
                "bloated_audio",
                "1080p BluRay releases with DTS-HD MA audio are considered bloated.",
                file_result,
            )
        )

    media_traits = file_result.get("traits") if isinstance(file_result.get("traits"), Mapping) else {}
    if str(media_traits.get("audio_format") or "") == "FLAC" and float(media_traits.get("audio_channels") or 0) > 2.0:
        issues.append(
            _issue(
                "ERROR",
                "bloated_audio",
                "FLAC audio is only allowed for mono or stereo releases.",
                file_result,
            )
        )

    default_language_raw = _language_track_text(default_audio) if isinstance(default_audio, Mapping) else ""
    default_language = normalize_language_label(default_language_raw)
    default_language_confident = language_is_confident(default_language_raw)
    audio_tracks = file_result.get("audio") if isinstance(file_result.get("audio"), list) else []
    audio_languages = {_language_name(track) for track in audio_tracks if isinstance(track, Mapping) and _language_name(track)}
    declared_languages = {normalize_language_label(language) for language in title_traits.languages if normalize_language_label(language)}
    if default_language and default_language != "english" and "english" in audio_languages:
        key = "primary_language" if default_language_confident else "primary_language_unverified"
        message = (
            f"Primary audio language is {default_language.title()}, but English audio is available."
            if default_language_confident
            else f'Primary audio language "{default_language_raw}" could not be confidently identified; review before upload.'
        )
        issues.append(_issue("ERROR", key, message, file_result))
    elif default_language and default_language != "english" and default_language not in declared_languages:
        key = "primary_language" if default_language_confident else "primary_language_unverified"
        message = (
            f"Primary audio language is {default_language.title()}, but the release name does not declare it."
            if default_language_confident
            else f'Primary audio language "{default_language_raw}" could not be confidently identified; review before upload.'
        )
        issues.append(_issue("ERROR", key, message, file_result))
    return issues


def _is_sample_video_file(path: str) -> bool:
    name = PurePosixPath(str(path or "")).name.lower()
    stem = PurePosixPath(name).stem
    return stem == "sample" or stem.endswith(".sample") or stem.endswith("-sample") or stem.endswith("_sample")


def _validate_hdr(title_traits: ReleaseTraits, media_traits: Mapping[str, Any], file_result: Mapping[str, Any]) -> List[Dict[str, Any]]:
    issues: List[Dict[str, Any]] = []
    title_formats = set(title_traits.hdr_formats)
    media_formats = set(media_traits.get("hdr_formats") or [])
    if "HDR10+" in title_formats and "HDR10+" not in media_formats:
        issues.append(_issue("ERROR", "hdr10plus_missing", "Name says HDR10+, but only HDR10/SDR metadata was found.", file_result))
    if "Dolby Vision" in title_formats and "Dolby Vision" not in media_formats:
        issues.append(_issue("ERROR", "dolby_vision_missing", "Name says Dolby Vision, but MediaInfo has no Dolby Vision metadata.", file_result))
    if "HDR10" in title_formats and "HDR10" not in media_formats and "HDR10+" not in media_formats:
        issues.append(_issue("ERROR", "hdr10_missing", "Name says HDR10/HDR, but MediaInfo has no HDR10 metadata.", file_result))
    if {"Dolby Vision", "HDR10"}.issubset(title_formats) and not {"Dolby Vision", "HDR10"}.issubset(media_formats):
        issues.append(_issue("ERROR", "dv_hdr10_fallback_missing", "Name says DV HDR10, but both Dolby Vision and HDR10 fallback were not found.", file_result))
    if "Dolby Vision" in media_formats and "HDR10" not in media_formats:
        profile = str(media_traits.get("dv_profile") or "")
        if profile != "DV P5":
            issues.append(_issue("WARNING", "dv_without_hdr10", "Dolby Vision has no HDR10 fallback metadata.", file_result))
    if media_formats and (not title_formats or "SDR" in title_formats):
        issues.append(_issue("WARNING", "hdr_unnamed", "File has HDR metadata but the release name has no HDR tag.", file_result))
    return issues


def _bitrate_warnings(
    title_traits: ReleaseTraits,
    video: Mapping[str, Any],
    audio: Mapping[str, Any],
    file_result: Mapping[str, Any],
) -> List[Dict[str, Any]]:
    issues: List[Dict[str, Any]] = []
    video_mbps = _bitrate_mbps(video)
    if video_mbps:
        for resolution, source, codec, rip_type, low, high in BITRATE_VIDEO_RULES:
            if title_traits.resolution != resolution:
                continue
            if title_traits.source != source:
                continue
            if codec and title_traits.codec != codec:
                continue
            if rip_type and title_traits.rip_type != rip_type:
                continue
            if video_mbps < low:
                issues.append(_issue("WARNING", "video_bitrate_low", f"Video bitrate {video_mbps:.1f} Mbps is below the usual {low:.1f} Mbps boundary.", file_result))
            elif video_mbps > high:
                issues.append(_issue("WARNING", "video_bitrate_high", f"Video bitrate {video_mbps:.1f} Mbps is above the usual {high:.1f} Mbps boundary.", file_result))
            break
    audio_kbps = _bitrate_kbps(audio)
    if audio_kbps:
        audio_format = str(title_traits.audio_format or "")
        for wanted_format, channels, atmos, low, high in BITRATE_AUDIO_RULES:
            if _audio_family(audio_format) != _audio_family(wanted_format):
                continue
            if channels is not None and title_traits.audio_channels != channels:
                continue
            if atmos and "Atmos" not in title_traits.audio_objects and "Atmos" not in audio_format:
                continue
            if audio_kbps < low:
                issues.append(_issue("WARNING", "audio_bitrate_low", f"Audio bitrate {audio_kbps:.0f} kbps is below the usual {low:.0f} kbps boundary.", file_result))
            elif audio_kbps > high:
                issues.append(_issue("WARNING", "audio_bitrate_high", f"Audio bitrate {audio_kbps:.0f} kbps is above the usual {high:.0f} kbps boundary.", file_result))
            break
    return issues


def _track_sanity_warnings(
    title_traits: ReleaseTraits,
    audios: Sequence[Any],
    texts: Sequence[Any],
    subtitle_count: int,
    default_audio: Mapping[str, Any],
    traits: Mapping[str, Any],
    file_result: Mapping[str, Any],
) -> List[Dict[str, Any]]:
    issues: List[Dict[str, Any]] = []
    default_title = _track_title(default_audio).lower()
    if "commentary" in default_title:
        issues.append(_issue("WARNING", "commentary_default", "Commentary track is marked default.", file_result))
    if audios and default_audio and audios[0] != default_audio:
        issues.append(_issue("WARNING", "main_audio_not_first", "Default/main audio track is not the first audio track.", file_result))
    languages = {_language_name(track) for track in audios if _language_name(track)}
    if languages and not {"english", "eng", "multi"}.intersection(languages) and not title_traits.languages:
        issues.append(_issue("WARNING", "no_english_audio", "No English audio track is marked on an English-looking release.", file_result))
    if "atmos" in default_title and "Atmos" not in traits.get("audio_objects", []):
        issues.append(_issue("WARNING", "atmos_title_without_metadata", "Audio title says Atmos but object/JOC metadata was not found.", file_result))
    forced_subs = [track for track in texts if "forced" in _track_title(track).lower() or _is_yes(_track_value(track, "Forced"))]
    for track in forced_subs:
        if not _is_yes(_track_value(track, "Default")) and not _is_yes(_track_value(track, "Forced")):
            issues.append(_issue("WARNING", "forced_subtitle_not_marked", "Forced subtitles exist but are not marked forced/default.", file_result))
            break
    if subtitle_count <= 0:
        issues.append(_issue("WARNING", "no_subtitles", "No subtitles were found in MediaInfo.", file_result))
    return issues


def _issue(severity: str, key: str, message: str, file_result: Optional[Mapping[str, Any]] = None) -> Dict[str, Any]:
    return {
        "severity": severity,
        "key": key,
        "message": message,
        "file": str((file_result or {}).get("name") or ""),
        "tags": list((file_result or {}).get("tags") or []),
    }


def _issues_as_flags(issues: Sequence[Mapping[str, Any]]) -> List[Dict[str, Any]]:
    flags = []
    for issue in issues:
        severity = str(issue.get("severity") or "").upper()
        if severity == "INFO":
            continue
        flags.append(
            {
                "key": str(issue.get("key") or ""),
                "label": "MediaInfo " + severity.title(),
                "severity": "blocker" if severity == "ERROR" else "warning",
                "detail": str(issue.get("message") or ""),
            }
        )
    return _dedupe_flags(flags)


def _media_reason(issues: Sequence[Mapping[str, Any]]) -> str:
    errors = [str(item.get("message") or "") for item in issues if item.get("severity") == "ERROR"]
    warnings = [str(item.get("message") or "") for item in issues if item.get("severity") == "WARNING"]
    if errors:
        return errors[0]
    if warnings:
        return f"MediaInfo confirmed with warning: {warnings[0]}"
    return "MediaInfo confirmed."


def _normalized_text(value: str) -> str:
    return re.sub(r"[\[\]{}()]+", " ", str(value or "").replace("_", ".")).lower()


def _parse_resolution(text: str) -> Tuple[str, str]:
    match = re.search(r"\b(8640|4320|2160|1440|1080|720|576|540|480|360|240)([pi])\b", text)
    if not match:
        return "", ""
    scan_type = "progressive" if match.group(2) == "p" else "interlaced"
    return f"{match.group(1)}{match.group(2)}", scan_type


def _parse_source(text: str) -> Tuple[str, str, str, str]:
    provider = ""
    for alias, label in PROVIDER_ALIASES.items():
        if re.search(rf"\b{re.escape(alias.lower())}\b", text):
            provider = label
            break
    if re.search(r"\b(?:remux)\b", text):
        return "bluray_remux", "REMUX", provider, "remux"
    if re.search(r"\b(?:web[ ._-]?dl|webdl)\b", text):
        return "web", "WEB-DL", provider, "web-dl"
    if re.search(r"\b(?:web[ ._-]?rip|webrip)\b", text):
        return "web", "WEBRip", provider, "webrip"
    if re.search(r"\bweb\b", text):
        return "web", "WEB", provider, "web"
    if re.search(r"\b(?:uhd[ ._-]?blu[ ._-]?ray|blu[ ._-]?ray|bluray|bd|bdrip|brrip|hddvd)\b", text):
        return "bluray_encode", "BluRay", provider, "encode"
    if re.search(r"\b(?:hdtv|uhdtv|dtv|pdtv|satrip|tvrip)\b", text):
        return "hdtv", "HDTV", provider, "hdtv"
    if re.search(r"\b(?:dvd|dvdr|dvdrip|dvd5|dvd9)\b", text):
        return "dvd", "DVD", provider, "dvd"
    if re.search(r"\b(?:cam|hdtc|tc|ts|r5|scr|dvdscr|wp|vhsrip|laserdisc|ld)\b", text):
        return "cam", "CAM/TS", provider, "cam"
    return "other", "", provider, ""


def _parse_hdr(text: str) -> Tuple[int, List[str], str]:
    has_dv = bool(re.search(r"\b(?:dv|dovi|dolby[ ._-]?vision)\b", text))
    has_hdr10plus = bool(re.search(r"(?:\bhdr10\s*\+|\bhdr10plus\b|\bhdr10p\b|\bhdr10\+\b)", text))
    has_hdr10 = bool(re.search(r"\b(?:hdr10|hdr|pq10|st2084)\b", text))
    has_hlg = bool(re.search(r"\bhlg\b", text))
    profile_match = re.search(r"\bdv[ ._-]?p?([578])\b|\bprofile[ ._-]?([578])\b", text)
    dv_profile = f"DV P{profile_match.group(1) or profile_match.group(2)}" if profile_match else ""
    formats: List[str] = []
    if has_dv:
        formats.append("Dolby Vision")
    if has_hdr10plus:
        formats.append("HDR10+")
    if has_hdr10:
        formats.append("HDR10")
    if has_hlg:
        formats.append("HLG")
    if not formats and re.search(r"\bsdr\b", text):
        formats.append("SDR")
    if has_dv and (has_hdr10plus or has_hdr10):
        return 4, formats, dv_profile
    if has_dv:
        return 3, formats, dv_profile
    if has_hdr10plus:
        return 2, formats, dv_profile
    if has_hdr10 or has_hlg:
        return 1, formats, dv_profile
    return 0, formats, dv_profile


def _parse_audio_format(text: str) -> Tuple[str, int]:
    checks = (
        ("TrueHD Atmos", r"\b(?:true[ ._-]?hd|truhd|mlp[ ._-]?fba)(?=[ ._-]?\d|\b)", r"\batmos\b"),
        ("DTS:X", r"\bdts[ ._:-]?x\b", None),
        ("DD+ Atmos", r"\b(?:ddp|dd\+|eac3|e[ ._-]?ac[ ._-]?3|dolby[ ._-]?digital[ ._-]?plus)(?=[ ._-]?\d|\b)", r"\batmos\b"),
        ("Atmos", r"\batmos\b", None),
        ("TrueHD", r"\b(?:true[ ._-]?hd|truhd|mlp[ ._-]?fba)(?=[ ._-]?\d|\b)", None),
        (
            "DTS-HD MA",
            r"\bdts[ ._-]?hd(?:[ ._-]?ma)?(?=[ ._-]?\d|\b)|\bdts[ ._-]?ma(?=[ ._-]?\d|\b)|\bdts[ ._-]?hd[ ._-]?master\b",
            None,
        ),
        ("FLAC", r"\bflac(?=[ ._-]?\d|\b)", None),
        ("PCM", r"\b(?:pcm|lpcm)(?=[ ._-]?\d|\b)", None),
        ("DTS-HD HRA", r"\bdts[ ._-]?hd[ ._-]?hra\b", None),
        ("DD+", r"\b(?:ddp|dd\+|eac3|e[ ._-]?ac[ ._-]?3|dolby[ ._-]?digital[ ._-]?plus)(?=[ ._-]?\d|\b)", None),
        ("DTS-ES", r"\bdts[ ._-]?es\b", None),
        ("DTS", r"\bdts(?=[ ._-]?\d|\b)", None),
        ("HE-AAC", r"\bhe[ ._-]?aac\b", None),
        ("AAC", r"\baac(?:[ ._-]?lc)?(?=[ ._-]?\d|\b)", None),
        ("DD", r"\b(?:dd(?!p)|ac3|ac[ ._-]?3|dolby[ ._-]?digital)(?=[ ._-]?\d|\b)", None),
        ("Opus", r"\bopus(?=[ ._-]?\d|\b)", None),
        ("Vorbis", r"\bvorbis\b|\bogg\b", None),
        ("MP2", r"\bmp2\b", None),
        ("MP3", r"\bmp3\b", None),
    )
    for label, required, secondary in checks:
        if re.search(required, text, flags=re.IGNORECASE) and (
            secondary is None or re.search(secondary, text, flags=re.IGNORECASE)
        ):
            return label, AUDIO_FORMAT_RANKS[label]
    return "", 0


def _parse_audio_channels(title: str) -> float:
    patterns = [
        r"(?:DDP?|DD\+|EAC3|E[ ._-]?AC[ ._-]?3|AC3|AAC|DTS(?:[ ._-]?HD)?(?:[ ._-]?MA)?|TRUEHD|TRUHD|FLAC|PCM|OPUS|MP3|ATMOS)[ ._-]*(\d{1,2})[ ._-]?([01])",
        r"(?:^|[ ._-])(\d{1,2})[.]([01])(?:[ ._-]|$)",
    ]
    for pattern in patterns:
        matches = re.findall(pattern, title, flags=re.IGNORECASE)
        values = [float(f"{major}.{minor}") for major, minor in matches if major in {"1", "2", "5", "6", "7", "9", "11"}]
        if values:
            return max(values)
    if re.search(r"\bmono\b", title, flags=re.IGNORECASE):
        return 1.0
    if re.search(r"\bstereo\b", title, flags=re.IGNORECASE):
        return 2.0
    return 0.0


def _parse_audio_objects(text: str, audio_format: str = "") -> List[str]:
    objects = []
    if re.search(r"\batmos\b|joc|object", text) or "Atmos" in audio_format:
        objects.append("Atmos")
    if re.search(r"\bdts[ ._:-]?x\b", text):
        objects.append("DTS:X")
    return objects


def _parse_codec(text: str) -> str:
    if re.search(r"\b(?:vvc|h[ ._-]?266|x266)\b", text):
        return "VVC"
    if re.search(r"\bav1\b", text):
        return "AV1"
    if re.search(r"\b(?:hevc|h[ ._-]?265|h265|x265)\b", text):
        return "HEVC"
    if re.search(r"\b(?:avc|h[ ._-]?264|h264|x264)\b", text):
        return "AVC"
    if re.search(r"\bvp9\b", text):
        return "VP9"
    if re.search(r"\bvp8\b", text):
        return "VP8"
    if re.search(r"\bmpeg[ ._-]?2\b", text):
        return "MPEG-2"
    if re.search(r"\bmpeg[ ._-]?4[ ._-]?visual\b", text):
        return "MPEG-4 Visual"
    if re.search(r"\bxvid\b", text):
        return "XviD"
    if re.search(r"\bdivx\b", text):
        return "DivX"
    if re.search(r"\bvc[ ._-]?1\b", text):
        return "VC-1"
    return ""


def _parse_movie_versions(text: str) -> List[str]:
    versions: List[str] = []
    for label, pattern in MOVIE_VERSION_PATTERNS:
        if re.search(pattern, text, flags=re.IGNORECASE):
            versions.append(label)
    if "IMAX Enhanced" in versions and "IMAX" in versions:
        versions.remove("IMAX")
    if "4K Remaster" in versions and "Remastered" in versions:
        versions.remove("Remastered")
    if "AI Upscale" in versions and "Upscale" in versions:
        versions.remove("Upscale")
    return versions


def _parse_season_episode(title: str) -> Tuple[Optional[int], Optional[int]]:
    match = re.search(r"\bS(\d{1,2})(?:E(\d{1,3}))?\b", title, flags=re.IGNORECASE)
    if not match:
        return None, None
    season = int(match.group(1))
    episode = int(match.group(2)) if match.group(2) else None
    return season, episode


def _parse_bit_depth(text: str) -> str:
    match = re.search(r"\b(8|10|12)[ ._-]?bit\b|\bhi10p\b", text)
    if not match:
        return ""
    if match.group(0).lower() == "hi10p":
        return "10-bit"
    return f"{match.group(1)}-bit"


def _parse_chroma(text: str) -> str:
    match = re.search(r"\b4[:.]?([24])[:.]?([024])\b", text)
    if not match:
        return ""
    return f"4:{match.group(1)}:{match.group(2)}"


def _parse_languages(text: str) -> List[str]:
    values = []
    checks = {
        "MULTi": r"\bmulti\b",
        "Dual Audio": r"\bdual[ ._-]?audio\b|\bdual\b",
        "English": r"\beng(?:lish)?\b",
        "German": r"\bger(?:man)?\b",
        "French": r"\bfre(?:nch)?\b",
        "Italian": r"\bita(?:lian)?\b",
        "Spanish": r"\bspa(?:nish)?\b",
        "Japanese": r"\bjpn|japanese\b",
        "Korean": r"\bkor|korean\b",
        "Russian": r"\brus|russian\b",
    }
    for label, pattern in checks.items():
        if re.search(pattern, text):
            values.append(label)
    return values


def _parse_subtitle_tags(text: str) -> List[str]:
    values = []
    checks = {
        "Subbed": r"\bsubbed\b",
        "Softsubs": r"\bsoftsubs?\b",
        "Hardsubs": r"\bhardsubs?\b|\bhcsubs?\b|\bhc\b",
        "Forced": r"\bforced\b",
        "External Subs": r"\bexternal\b",
    }
    for label, pattern in checks.items():
        if re.search(pattern, text):
            values.append(label)
    return values


def _parse_container(text: str) -> str:
    match = re.search(r"\b(mkv|mp4|m4v|avi|ts|m2ts|mov|wmv|iso)\b", text)
    return match.group(1).upper() if match else ""


def _custom_formats_from_tags(
    tags: Sequence[str],
    source: str,
    codec: str,
    audio_format: str,
    hdr_formats: Sequence[str],
    movie_versions: Sequence[str],
) -> List[str]:
    values = list(tags)
    if source == "bluray_remux":
        values.append("Remux")
    if codec == "HEVC":
        values.append("HEVC/x265")
    if codec == "AVC":
        values.append("AVC/x264")
    if audio_format:
        values.append(audio_format)
    values.extend(hdr_formats)
    values.extend(movie_versions)
    return _dedupe(values)


def _media_file_labels(
    files: Sequence[Mapping[str, Any]],
    *,
    key: str,
    suppressed: set[str],
) -> List[str]:
    labels: List[str] = []
    for file_info in files:
        if not isinstance(file_info, Mapping):
            continue
        values = file_info.get(key)
        if not isinstance(values, (list, tuple)):
            continue
        for value in values:
            label = str(value or "")
            if not label or _label_is_suppressed(label, suppressed):
                continue
            labels.append(label)
    return _dedupe(labels)


def _title_display_tags(title_traits: ReleaseTraits) -> List[str]:
    return _dedupe([*title_traits.tags, *title_traits.custom_formats])


def _title_tag_matches(title_traits: ReleaseTraits, title_tags: Sequence[str], media_tags: Sequence[str]) -> List[Dict[str, str]]:
    return [
        {
            "label": tag,
            "state": state,
            "reason": _title_tag_reason(state),
        }
        for tag in title_tags
        for state in [_title_tag_state(tag, title_traits, media_tags)]
    ]


def _title_tag_reason(state: str) -> str:
    if state == "match":
        return "Confirmed by MediaInfo."
    if state == "mismatch":
        return "Not confirmed by MediaInfo."
    return "Not directly verified by MediaInfo."


def _title_tag_state(tag: str, title_traits: ReleaseTraits, media_tags: Sequence[str]) -> str:
    category = _title_tag_category(tag, title_traits)
    if category == "neutral":
        return "neutral"
    return "match" if _title_tag_confirmed(tag, category, media_tags) else "mismatch"


def _title_tag_category(tag: str, title_traits: ReleaseTraits) -> str:
    if tag == title_traits.resolution:
        return "resolution"
    if tag == title_traits.scan_type:
        return "scan_type"
    if tag == title_traits.codec or tag in _codec_display_aliases(title_traits.codec):
        return "codec"
    if tag == title_traits.audio_format:
        return "audio_format"
    if tag == _format_channels(title_traits.audio_channels):
        return "audio_channels"
    if tag in title_traits.audio_objects:
        return "audio_object"
    if tag in title_traits.hdr_formats:
        return "hdr_format"
    if tag == title_traits.dv_profile:
        return "dv_profile"
    if tag == title_traits.bit_depth:
        return "bit_depth"
    if tag == title_traits.chroma:
        return "chroma"
    if tag in title_traits.languages:
        return "language"
    if tag in title_traits.subtitle_tags:
        return "subtitle"
    return "neutral"


def _title_tag_confirmed(tag: str, category: str, media_tags: Sequence[str]) -> bool:
    media = {str(value) for value in media_tags if str(value)}
    lowered = {value.lower() for value in media}
    if category == "codec":
        return bool(media.intersection(_codec_confirmation_labels(tag)))
    if category == "audio_format":
        if "Atmos" in tag or tag == "DTS:X":
            return tag in media
        return any(_audio_family(value) == _audio_family(tag) for value in media)
    if category == "hdr_format" and tag == "HDR10":
        return "HDR10" in media or "HDR10+" in media
    if category == "subtitle":
        return tag in media or (tag in {"Subbed", "Softsubs", "External Subs"} and "Subtitles" in media)
    if category == "language":
        return tag.lower() in lowered
    return tag in media


def _codec_confirmation_labels(tag: str) -> set[str]:
    codec = tag
    if tag == "HEVC/x265":
        codec = "HEVC"
    elif tag == "AVC/x264":
        codec = "AVC"
    labels = {codec}
    labels.update(_codec_display_aliases(codec))
    return labels


def _codec_display_aliases(codec: str) -> set[str]:
    if codec == "HEVC":
        return {"HEVC/x265"}
    if codec == "AVC":
        return {"AVC/x264"}
    return set()


def _label_is_suppressed(label: str, suppressed: set[str]) -> bool:
    if label in suppressed:
        return True
    aliases = set()
    for value in suppressed:
        aliases.update(_codec_display_aliases(value))
        aliases.update(_codec_confirmation_labels(value) if value in {"HEVC/x265", "AVC/x264"} else set())
    return label in aliases


def _suppressed_display_labels_from_issues(issues: Sequence[Any]) -> List[str]:
    labels: List[str] = []
    for issue in issues:
        if not isinstance(issue, Mapping) or str(issue.get("key") or "") != "mediainfo_provider_disagreement":
            continue
        message = str(issue.get("message") or "")
        labels.extend(value.strip() for value in re.findall(r"(?:QUI|Local MediaInfo)=([^,;.]+)", message) if value.strip())
    return _dedupe(labels)


def _hdr_from_mediainfo(video: Mapping[str, Any], fallback: ReleaseTraits) -> Tuple[int, List[str], str]:
    hdr_format_text = _joined_track_text(
        video,
        "HDR_Format",
        "HDR_Format_String",
        "HDR_Format_Commercial",
        "HDR_Format_Compatibility",
        "HDR_Format_Profile",
        "HDR_Format_Settings",
    ).lower()
    color_text = _joined_track_text(
        video,
        "MasteringDisplay_ColorPrimaries",
        "MasteringDisplay_Luminance",
        "MaxCLL",
        "MaxFALL",
        "colour_primaries",
        "transfer_characteristics",
        "matrix_coefficients",
        "ColorPrimaries",
        "TransferCharacteristics",
        "MatrixCoefficients",
    ).lower()
    descriptive_text = _joined_track_text(
        video,
        "Title",
        "title",
        "Format",
        "Format_Profile",
        "Format_Commercial_IfAny",
        "CodecID",
        "CodecID_Info",
        "CodecID/Info",
        "CodecID_Compatible",
        "Codec",
    ).lower()
    text = " ".join(value for value in (hdr_format_text, color_text, descriptive_text) if value)
    bit_depth = _number_from_value(_track_value(video, "BitDepth", "bitDepth"))
    transfer = _track_text(video, "transfer_characteristics", "TransferCharacteristics").lower()
    primaries = _track_text(video, "colour_primaries", "ColorPrimaries").lower()
    static_hdr_metadata = bool(
        _joined_track_text(
            video,
            "MasteringDisplay_ColorPrimaries",
            "MasteringDisplay_Luminance",
            "MaxCLL",
            "MaxFALL",
        )
    )
    has_dv = bool(
        "dolby vision" in text
        or re.search(r"\bdv\b|dovi", text)
        or re.search(r"\b(?:dvhe|dvh1|dvav)[. ]?0?[578]\b", text)
        or re.search(r"\bdvh1\b", text)
        or "bl+rpu" in text
    )
    has_hdr10plus = "hdr10+" in text or "smpte st 2094" in text or "dynamic metadata" in text
    explicit_hdr10 = (
        "hdr10" in hdr_format_text
        or "smpte st 2086" in text
        or "mastering display" in text
        or "maxcll" in text
        or (bit_depth >= 10 and static_hdr_metadata)
    )
    inferred_hdr10 = (
        bit_depth >= 10
        and "bt.2020" in primaries
        and ("pq" in transfer or "st 2084" in transfer or "st2084" in transfer)
    )
    has_hdr10 = explicit_hdr10 or inferred_hdr10
    has_hlg = "hlg" in text
    profile_match = re.search(r"profile[^\d]*([578])|(?:dvhe|dvh1|dvav)[. ]0?([578])", text)
    dv_profile = f"DV P{profile_match.group(1) or profile_match.group(2)}" if profile_match else fallback.dv_profile
    formats = []
    if has_dv:
        formats.append("Dolby Vision")
    if has_hdr10plus:
        formats.append("HDR10+")
    if has_hdr10:
        formats.append("HDR10")
    if has_hlg:
        formats.append("HLG")
    if not formats:
        formats = list(fallback.hdr_formats)
    if "Dolby Vision" in formats and ("HDR10+" in formats or "HDR10" in formats):
        return 4, formats, dv_profile
    if "Dolby Vision" in formats:
        return 3, formats, dv_profile
    if "HDR10+" in formats:
        return 2, formats, dv_profile
    if "HDR10" in formats or "HLG" in formats:
        return 1, formats, dv_profile
    return fallback.hdr_rank, formats, dv_profile


def _codec_from_mediainfo(video: Mapping[str, Any], fallback: str) -> str:
    text = _joined_track_text(
        video,
        "Format",
        "Format_Commercial_IfAny",
        "CodecID",
        "Encoded_Library_Name",
        "Encoded_Library",
    ).lower()
    parsed = _parse_codec(text)
    return parsed or fallback


def _audio_format_from_mediainfo(audio: Mapping[str, Any], fallback: str) -> str:
    text = _joined_track_text(
        audio,
        "Format",
        "Format_Commercial_IfAny",
        "CommercialName",
        "Commercial name",
        "Format_Profile",
        "Format_AdditionalFeatures",
        "CodecID",
    ).lower()
    parsed, _rank = _parse_audio_format(text)
    if parsed == "Atmos" and fallback:
        return fallback
    if parsed:
        title_text = _joined_track_text(audio, "Title", "title").lower()
        if "atmos" in title_text and parsed in {"TrueHD", "DD+"}:
            return f"{parsed} Atmos"
        return parsed
    title_text = _joined_track_text(audio, "Title", "title").lower()
    title_parsed, _title_rank = _parse_audio_format(title_text)
    if title_parsed == "Atmos" and fallback:
        return fallback
    return title_parsed or fallback


def _audio_objects_from_mediainfo(audio: Mapping[str, Any], fallback: Sequence[str]) -> List[str]:
    text = _joined_scalar_text(audio).lower()
    values = _parse_audio_objects(text)
    return values


def _audio_objects_from_tracks(audios: Sequence[Mapping[str, Any]]) -> List[str]:
    values: List[str] = []
    for audio in audios:
        values.extend(_audio_objects_from_mediainfo(audio, ()))
    return _dedupe(values)


def _mediainfo_channels(audio: Mapping[str, Any]) -> float:
    value = _track_text(audio, "Channels", "channels", "Channel(s)")
    layout = _joined_track_text(audio, "ChannelLayout", "ChannelLayout_Original").lower()
    match = re.search(r"\d+(?:\.\d+)?", value)
    if not match:
        layout_channels = _channels_from_layout(layout)
        if layout_channels:
            return layout_channels
        return 0.0
    channels = float(match.group(0))
    if channels == 1:
        return 1.0
    if channels == 2:
        return 2.0
    layout_channels = _channels_from_layout(layout, channels)
    if layout_channels:
        return layout_channels
    if channels == 6:
        return 5.1
    if channels == 8:
        return 7.1
    return channels


def _channels_from_layout(layout: str, channels: float = 0.0) -> float:
    if not layout:
        return 0.0
    tokens = re.findall(r"[a-z0-9]+", layout.lower())
    if not tokens:
        return 0.0
    has_lfe = "lfe" in tokens
    if channels and has_lfe and channels >= 2:
        return round((channels - 1) + 0.1, 1)
    token_count = len([token for token in tokens if token not in {"unknown"}])
    if has_lfe and token_count >= 2:
        return round((token_count - 1) + 0.1, 1)
    return float(token_count) if token_count else 0.0


def _resolution_from_dimensions(width: int, height: int, scan: str) -> str:
    suffix = "i" if scan.startswith("inter") else "p"
    if not height and not width:
        return ""
    if width >= 7000 or height >= 4000:
        return f"4320{suffix}"
    if width >= 3000 or height >= 1500:
        return f"2160{suffix}"
    if width >= 2200 or height >= 1100:
        return f"1440{suffix}"
    if width >= 1600 or height >= 760:
        return f"1080{suffix}"
    if width >= 1100 or height >= 600:
        return f"720{suffix}"
    if height >= 540:
        return f"576{suffix}"
    if height >= 450:
        return f"480{suffix}"
    if height >= 330:
        return f"360{suffix}"
    if height >= 200:
        return f"240{suffix}"
    return f"{height}{suffix}" if height else ""


def _resolution_matches(expected: str, actual: str, video: Mapping[str, Any]) -> bool:
    expected_height = _resolution_height_int(expected)
    actual_height = _resolution_height_int(actual)
    if not expected_height or not actual_height:
        return True
    if expected_height == actual_height:
        return True
    width = _number_from_value(_track_value(video, "width", "Width"))
    lower = int(expected_height * 0.70)
    upper = expected_height + 24
    if lower <= actual_height <= upper:
        expected_width = {2160: 3840, 1440: 2560, 1080: 1920, 720: 1280, 576: 720, 480: 720}.get(expected_height, 0)
        if not expected_width or width >= int(expected_width * 0.82):
            return True
    return False


def _track_payload(track: Mapping[str, Any]) -> Dict[str, Any]:
    if not track:
        return {}
    return {
        "type": _track_text(track, "@type", "type"),
        "format": _track_text(track, "Format", "format"),
        "commercial": _track_text(track, "Format_Commercial_IfAny", "CommercialName", "Commercial name"),
        "profile": _track_text(track, "Format_Profile"),
        "title": _track_title(track),
        "language": _language_name(track),
        "channels": _track_text(track, "Channels", "channels"),
        "bitrate": _track_text(track, "BitRate", "bitRate", "BitRate_String"),
        "default": _track_text(track, "Default"),
        "forced": _track_text(track, "Forced"),
        "width": _number_from_value(_track_value(track, "Width", "width")),
        "height": _number_from_value(_track_value(track, "Height", "height")),
    }


def _first_track(tracks: Sequence[Mapping[str, Any]], track_type: str) -> Mapping[str, Any]:
    for track in tracks:
        if _track_type(track) == track_type:
            return track
    return {}


def _tracks_by_type(tracks: Sequence[Mapping[str, Any]], track_type: str) -> List[Mapping[str, Any]]:
    return [track for track in tracks if _track_type(track) == track_type]


def _default_track(tracks: Sequence[Mapping[str, Any]]) -> Mapping[str, Any]:
    for track in tracks:
        if _is_yes(_track_value(track, "Default")):
            return track
    return {}


def _track_languages(tracks: Sequence[Mapping[str, Any]]) -> List[str]:
    return _dedupe([_language_name(track) for track in tracks if _language_name(track)])


def _subtitle_count(tracks: Sequence[Mapping[str, Any]]) -> int:
    text_tracks = len(_tracks_by_type(tracks, "text"))
    for track in tracks:
        if _track_type(track) != "general":
            continue
        count = _number_from_value(_track_value(track, "TextCount", "textCount", "Text_Count"))
        if count:
            return max(text_tracks, count)
    return text_tracks


def _subtitle_tags_from_tracks(tracks: Sequence[Mapping[str, Any]], subtitle_count: int = 0) -> List[str]:
    tags = ["Subtitles"] if tracks or subtitle_count > 0 else []
    for track in tracks:
        title = _track_title(track).lower()
        if "forced" in title or _is_yes(_track_value(track, "Forced")):
            tags.append("Forced")
        if _is_yes(_track_value(track, "Default")):
            tags.append("Default Subs")
    return _dedupe(tags)


def _track_title(track: Mapping[str, Any]) -> str:
    return _track_text(track, "Title", "title")


def _language_name(track: Mapping[str, Any]) -> str:
    return normalize_language_label(_language_track_text(track))


def _language_track_text(track: Mapping[str, Any]) -> str:
    return _track_text(track, "Language", "language").strip()


def _track_type(track: Mapping[str, Any]) -> str:
    return _track_text(track, "@type", "type").lower()


def _track_text(track: Mapping[str, Any], *keys: str) -> str:
    value = _track_value(track, *keys)
    return str(value or "").strip()


def _joined_track_text(track: Mapping[str, Any], *keys: str) -> str:
    return " ".join(_track_text(track, key) for key in keys if _track_text(track, key))


def _joined_scalar_text(value: Any) -> str:
    parts: List[str] = []

    def collect(item: Any) -> None:
        if isinstance(item, Mapping):
            for key, nested in item.items():
                parts.append(str(key))
                collect(nested)
        elif isinstance(item, list):
            for nested in item:
                collect(nested)
        elif isinstance(item, (str, int, float)):
            parts.append(str(item))

    collect(value)
    return " ".join(parts)


def _track_value(track: Mapping[str, Any], *keys: str) -> Any:
    return _mapping_value(track, *keys)


def _mapping_value(mapping: Mapping[str, Any], *keys: str) -> Any:
    if not isinstance(mapping, Mapping):
        return None
    for key in keys:
        if key in mapping and mapping[key] is not None:
            return mapping[key]
    lowered = {str(key).lower(): value for key, value in mapping.items()}
    for key in keys:
        value = lowered.get(str(key).lower())
        if value is not None:
            return value
    compacted = {_compact_mapping_key(str(key)): value for key, value in mapping.items()}
    for key in keys:
        value = compacted.get(_compact_mapping_key(str(key)))
        if value is not None:
            return value
    return None


def _compact_mapping_key(value: str) -> str:
    return re.sub(r"[^a-z0-9]+", "", value.lower()).replace("colour", "color")


def _is_yes(value: Any) -> bool:
    return str(value or "").strip().lower() in {"yes", "true", "1"}


def _first_text(track: Mapping[str, Any], *keys: str) -> str:
    for key in keys:
        value = _track_text(track, key)
        if value:
            return value
    return ""


def _number_from_value(value: Any) -> int:
    text = re.sub(r"(?<=\d)\s+(?=\d{3}\b)", "", str(value or ""))
    match = re.search(r"\d+", text)
    return int(match.group(0)) if match else 0


def _bitrate_mbps(track: Mapping[str, Any]) -> float:
    value = _bitrate_bps(track)
    return value / 1_000_000 if value else 0.0


def _bitrate_kbps(track: Mapping[str, Any]) -> float:
    value = _bitrate_bps(track)
    return value / 1_000 if value else 0.0


def _bitrate_bps(track: Mapping[str, Any]) -> float:
    raw = _track_text(track, "bitrate", "BitRate", "BitRate_String")
    if not raw:
        return 0.0
    match = re.search(r"([\d.]+)", raw.replace(" ", ""))
    if not match:
        return 0.0
    amount = float(match.group(1))
    lowered = raw.lower()
    if "mb/s" in lowered or "mbps" in lowered:
        return amount * 1_000_000
    if "kb/s" in lowered or "kbps" in lowered:
        return amount * 1_000
    return amount


def _audio_family(value: str) -> str:
    if value in {"DD+ Atmos"}:
        return "DD+"
    if value in {"TrueHD Atmos"}:
        return "TrueHD"
    return value


def _format_channels(value: float) -> str:
    return f"{value:.1f}" if value else ""


def _resolution_height(value: str) -> str:
    match = re.match(r"(\d+)", value or "")
    return match.group(1) if match else ""


def _resolution_height_int(value: str) -> int:
    height = _resolution_height(value)
    return int(height) if height else 0


def _scan_rank(traits: ReleaseTraits) -> int:
    if traits.scan_type == "progressive":
        return 2
    if traits.scan_type == "interlaced":
        return 1
    return 0


def _string_sequence(value: Any) -> List[str]:
    if value is None:
        return []
    if isinstance(value, str):
        return [value] if value else []
    if isinstance(value, Sequence):
        return [str(item) for item in value if str(item)]
    return [str(value)] if str(value) else []


def _int_payload_value(value: Any) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return 0


def _optional_int_payload_value(value: Any) -> Optional[int]:
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _float_payload_value(value: Any) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return 0.0


def _bool_payload_value(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        return value.strip().lower() in {"1", "true", "yes", "y"}
    return bool(value)


def _dedupe(values: Sequence[str]) -> List[str]:
    result = []
    seen = set()
    for value in values:
        text = str(value or "").strip()
        if not text or text.lower() in seen:
            continue
        seen.add(text.lower())
        result.append(text)
    return result


def _dedupe_flags(flags: Sequence[Mapping[str, Any]]) -> List[Dict[str, Any]]:
    seen = set()
    result = []
    for flag in flags:
        key = str(flag.get("key") or "")
        detail = str(flag.get("detail") or "")
        dedupe_key = (key, detail)
        if not key or dedupe_key in seen:
            continue
        seen.add(dedupe_key)
        result.append(dict(flag))
    return result
