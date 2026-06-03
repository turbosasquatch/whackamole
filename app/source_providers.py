from __future__ import annotations

import re
import unicodedata
from typing import Iterable, List, Optional, Sequence, Tuple


Provider = Tuple[str, str, Sequence[str]]


PROVIDERS: Sequence[Provider] = (
    ("9NOW", "9Now", ("9now",)),
    ("AE", "A&E", ("a&e", "a and e")),
    ("AUBC", "ABC (AU) iView", ("abc au iview", "abc iview", "iview")),
    ("AMBC", "ABC (US)", ("abc us",)),
    ("AS", "Adult Swim", ("adult swim",)),
    ("AJAZ", "Al Jazeera English", ("al jazeera english", "al jazeera")),
    ("ALL4", "All4", ("all4", "channel 4", "4od")),
    ("AMZN", "Amazon", ("amazon", "prime video", "amazon prime video")),
    ("AMC", "AMC", ("amc",)),
    ("ATK", "America's Test Kitchen", ("america's test kitchen", "americas test kitchen")),
    ("ANPL", "Animal Planet", ("animal planet",)),
    ("ANLB", "AnimeLab", ("animelab",)),
    ("AOL", "AOL", ("aol",)),
    ("ATVP", "Apple TV+", ("apple tv+", "apple tv plus", "apple tv")),
    ("ARD", "ARD", ("ard",)),
    ("iP", "BBC iPlayer", ("bbc iplayer", "iplayer")),
    ("BNGE", "Binge", ("binge",)),
    ("BKPL", "Blackpills", ("blackpills",)),
    ("BOOM", "Boomerang", ("boomerang",)),
    ("BRAV", "BravoTV", ("bravotv", "bravo tv")),
    ("CMOR", "C More", ("c more",)),
    ("CNLP", "Canal+", ("canal+", "canal plus")),
    ("CN", "Cartoon Network", ("cartoon network",)),
    ("CBC", "CBC", ("cbc",)),
    ("CBS", "CBS", ("cbs",)),
    ("CHGD", "CHRGD", ("chrgd",)),
    ("CMAX", "Cinemax", ("cinemax",)),
    ("CLBI", "Club illico", ("club illico",)),
    ("CNBC", "CNBC", ("cnbc",)),
    ("CCGC", "Comedians in Cars Getting Coffee", ("comedians in cars getting coffee",)),
    ("CC", "Comedy Central", ("comedy central",)),
    ("COOK", "Cooking Channel", ("cooking channel",)),
    ("CMT", "Country Music Television", ("country music television", "cmt")),
    ("CRKL", "Crackle", ("crackle",)),
    ("CRAV", "Crave", ("crave",)),
    ("CRIT", "Criterion Channel", ("criterion channel",)),
    ("CR", "Crunchyroll", ("crunchyroll", "crunchy roll")),
    ("CSPN", "CSpan", ("cspan", "c-span")),
    ("CTV", "CTV", ("ctv",)),
    ("CUR", "CuriosityStream", ("curiositystream", "curiosity stream")),
    ("CW", "The CW", ("the cw",)),
    ("CWS", "CWSeed", ("cwseed", "cw seed")),
    ("DSKI", "Daisuki", ("daisuki",)),
    ("DCU", "DC Universe", ("dc universe",)),
    ("DHF", "Deadhouse Films", ("deadhouse films",)),
    ("DEST", "Destination America", ("destination america",)),
    ("DDY", "Digiturk Diledigin Yerde", ("digiturk diledigin yerde", "digiturk diledigin yerde")),
    ("DTV", "DirecTV Now", ("directv now",)),
    ("DISC", "Discovery Channel", ("discovery channel",)),
    ("DSCP", "Discovery+", ("discovery+", "discovery plus")),
    ("DSNY", "Disney", ("disney",)),
    ("DSNP", "Disney+", ("disney+", "disney plus")),
    ("DIY", "DIY Network", ("diy network",)),
    ("DOCC", "Doc Club", ("doc club",)),
    ("DPLY", "DPlay", ("dplay",)),
    ("DF", "DramaFever", ("dramafever", "drama fever")),
    ("DRPO", "Dropout", ("dropout",)),
    ("DRTV", "DRTV", ("drtv",)),
    ("ETV", "E!", ("e!",)),
    ("ETTV", "El Trece", ("el trece",)),
    ("EPIX", "EPIX", ("epix",)),
    ("ESPN", "ESPN", ("espn",)),
    ("ESQ", "Esquire", ("esquire",)),
    ("FAM", "Family", ("family",)),
    ("FJR", "Family Jr", ("family jr",)),
    ("FOOD", "Food Network", ("food network",)),
    ("FOX", "Fox", ("fox",)),
    ("FXTL", "Foxtel Now", ("foxtel now",)),
    ("FPT", "FPT Play", ("fpt play",)),
    ("FTV", "France.tv", ("france.tv", "france tv")),
    ("FREE", "Freeform", ("freeform",)),
    ("FUNI", "Funimation", ("funimation",)),
    ("FYI", "FYI Network", ("fyi network",)),
    ("GLBL", "Global", ("global",)),
    ("GLOB", "GloboSat Play", ("globosat play",)),
    ("GO90", "go90", ("go90",)),
    ("PLAY", "Google Play", ("google play",)),
    ("HLMK", "Hallmark", ("hallmark",)),
    ("HBO", "HBO", ("hbo",)),
    ("HMAX", "HBO Max", ("hbo max", "hbomax")),
    ("HGTV", "HGTV", ("hgtv",)),
    ("HIDI", "HIDIVE", ("hidive",)),
    ("HIST", "History Channel", ("history channel",)),
    ("HTSR", "Hotstar", ("hotstar",)),
    ("HULU", "Hulu", ("hulu",)),
    ("TOU", "Ici TOU.TV", ("ici tou.tv", "tou.tv", "ici tou tv")),
    ("IFC", "IFC", ("ifc",)),
    ("ID", "Investigation Discovery", ("investigation discovery",)),
    ("iT", "iTunes", ("itunes",)),
    ("ITV", "ITV", ("itv",)),
    ("KNPY", "Kanopy", ("kanopy",)),
    ("KAYO", "Kayo Sports", ("kayo sports",)),
    ("KNOW", "Knowledge Network", ("knowledge network",)),
    ("LIFE", "Lifetime", ("lifetime",)),
    ("LN", "Loving Nature", ("loving nature",)),
    ("MAX", "Max", ("max",)),
    ("MBC", "MBC", ("mbc",)),
    ("MTOD", "Motor Trend OnDemand", ("motor trend ondemand", "motortrend ondemand")),
    ("MNBC", "MSNBC", ("msnbc",)),
    ("MTV", "MTV", ("mtv",)),
    ("NATG", "National Geographic", ("national geographic",)),
    ("NBA", "NBA League Pass", ("nba league pass",)),
    ("NBC", "NBC", ("nbc",)),
    ("NF", "Netflix", ("netflix",)),
    ("NFL", "NFL Network", ("nfl network",)),
    ("NFLN", "NFL Now", ("nfl now",)),
    ("GC", "NHL GameCenter", ("nhl gamecenter", "nhl game center")),
    ("NICK", "Nickelodeon", ("nickelodeon",)),
    ("NRK", "Norsk Rikskringkasting", ("norsk rikskringkasting",)),
    ("NOW", "Now", ("now", "sky now")),
    ("ODK", "OnDemandKorea", ("ondemandkorea", "on demand korea")),
    ("OXGN", "Oxygen", ("oxygen",)),
    ("PMNT", "Paramount Network", ("paramount network",)),
    ("PMTP", "Paramount+", ("paramount+", "paramount plus")),
    ("PBS", "PBS", ("pbs",)),
    ("PBSK", "PBS Kids", ("pbs kids",)),
    ("PCOK", "Peacock", ("peacock",)),
    ("PSN", "Playstation Network", ("playstation network",)),
    ("PLUZ", "Pluzz", ("pluzz",)),
    ("POGO", "PokerGo", ("pokergo", "poker go")),
    ("PA", "Project Alpha", ("project alpha",)),
    ("PUHU", "puhutv", ("puhutv",)),
    ("QIBI", "Quibi", ("quibi",)),
    ("RKTN", "Rakuten TV", ("rakuten tv",)),
    ("ROKU", "The Roku Channel", ("the roku channel", "roku channel")),
    ("RSTR", "Rooster Teeth", ("rooster teeth",)),
    ("RTE", "RTE", ("rte",)),
    ("SBS", "SBS", ("sbs",)),
    ("SESO", "Seeso", ("seeso",)),
    ("SHMI", "Shomi", ("shomi",)),
    ("SHO", "Showtime", ("showtime",)),
    ("SHDR", "Shudder", ("shudder",)),
    ("SKST", "SkyShowtime", ("skyshowtime", "sky showtime")),
    ("SPIK", "Spike", ("spike",)),
    ("SNET", "Sportsnet", ("sportsnet",)),
    ("SPRT", "Sprout", ("sprout",)),
    ("STAN", "Stan", ("stan",)),
    ("STRP", "Star+", ("star+", "star plus")),
    ("STZ", "Starz", ("starz",)),
    ("SVT", "Sveriges Television", ("sveriges television",)),
    ("SWER", "SwearNet", ("swearnet",)),
    ("SYFY", "SyFy", ("syfy",)),
    ("TBS", "TBS", ("tbs",)),
    ("TEN", "TenPlay", ("tenplay",)),
    ("TFOU", "TFOU", ("tfou",)),
    ("TIMV", "TIMvision", ("timvision",)),
    ("TLC", "TLC", ("tlc",)),
    ("TRVL", "Travel Channel", ("travel channel",)),
    ("TUBI", "TubiTV", ("tubitv", "tubi tv", "tubi")),
    ("TV3", "TV3", ("tv3",)),
    ("TV4", "TV4", ("tv4",)),
    ("TVING", "TVING", ("tving",)),
    ("TVL", "TVLand", ("tvland", "tv land")),
    ("UFC", "UFC", ("ufc",)),
    ("UKTV", "UKTV", ("uktv",)),
    ("UNIV", "Univision", ("univision",)),
    ("USAN", "USA Network", ("usa network",)),
    ("VLCT", "Velocity", ("velocity",)),
    ("VTRN", "VET Tv", ("vet tv",)),
    ("VH1", "VH1", ("vh1",)),
    ("VIAP", "Viaplay", ("viaplay",)),
    ("VICE", "Viceland", ("viceland",)),
    ("VIKI", "Viki", ("viki",)),
    ("VMEO", "Vimeo", ("vimeo",)),
    ("VRV", "VRV", ("vrv",)),
    ("WNET", "W Network", ("w network",)),
    ("WME", "WatchMe", ("watchme", "watch me")),
    ("WWEN", "WWE Network", ("wwe network",)),
    ("XBOX", "Xbox Video", ("xbox video",)),
    ("YHOO", "Yahoo", ("yahoo",)),
    ("YT", "YouTube Movies", ("youtube movies",)),
    ("RED", "YouTube Red", ("youtube red",)),
    ("ZDF", "ZDF", ("zdf",)),
)


SOURCE_MARKERS = re.compile(r"\b(?:site|network|source|service|provider|streaming)\b", re.IGNORECASE)
WEB_TITLE_TOKENS = {"WEB", "WEBDL", "WEBRIP", "WEBHD"}
PROVIDER_BY_UPPER_ABBREVIATION = {abbreviation.upper(): abbreviation for abbreviation, _name, _aliases in PROVIDERS}


def extract_provider_abbreviation(*texts: str) -> str:
    candidates = [str(text or "") for text in texts if str(text or "").strip()]
    marker_lines: List[str] = []
    for text in candidates:
        marker_lines.extend(line for line in text.splitlines() if SOURCE_MARKERS.search(line))
    for text in [*marker_lines, *candidates]:
        match = _match_provider(text, allow_short_abbreviations=text in marker_lines)
        if match:
            return match
    return ""


def extract_provider_from_release_title(title: str) -> str:
    """Extract a provider token from common release-title source positions."""
    tokens = re.findall(r"[A-Za-z0-9+]+", str(title or ""))
    if not tokens:
        return ""
    normalized_tokens = [token.upper().replace("+", "") for token in tokens]
    candidate_indexes = set()
    for index, token in enumerate(normalized_tokens):
        if token not in WEB_TITLE_TOKENS:
            continue
        for offset in (-2, -1, 1, 2):
            candidate_indexes.add(index + offset)
    for index in sorted(candidate_indexes):
        if index < 0 or index >= len(normalized_tokens):
            continue
        provider = PROVIDER_BY_UPPER_ABBREVIATION.get(normalized_tokens[index])
        if provider:
            return provider
    return ""


def _match_provider(text: str, *, allow_short_abbreviations: bool) -> str:
    normalized = _normalize(text)
    if not normalized:
        return ""
    aliases: List[Tuple[str, str, bool]] = []
    for abbreviation, name, extra_aliases in PROVIDERS:
        aliases.append((_normalize(abbreviation), abbreviation, True))
        aliases.append((_normalize(name), abbreviation, False))
        aliases.extend((_normalize(alias), abbreviation, False) for alias in extra_aliases)
    aliases.sort(key=lambda item: len(item[0]), reverse=True)
    for alias, abbreviation, is_abbreviation in aliases:
        if not alias:
            continue
        if is_abbreviation and len(alias) < 3 and not allow_short_abbreviations:
            continue
        if _contains_alias(normalized, alias):
            return abbreviation
    return ""


def _contains_alias(text: str, alias: str) -> bool:
    if not alias:
        return False
    pattern = rf"(?<![a-z0-9]){re.escape(alias)}(?![a-z0-9])"
    return bool(re.search(pattern, text))


def _normalize(value: str) -> str:
    decomposed = unicodedata.normalize("NFKD", str(value or ""))
    ascii_text = decomposed.encode("ascii", "ignore").decode("ascii")
    return re.sub(r"[^a-z0-9+&.!]+", " ", ascii_text.lower()).strip()


def provider_abbreviation_for_label(label: str) -> str:
    return extract_provider_abbreviation(f"source: {label}")


def provider_abbreviations() -> Iterable[str]:
    return (abbreviation for abbreviation, _name, _aliases in PROVIDERS)
