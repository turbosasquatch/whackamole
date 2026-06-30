from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any, Dict, Mapping, Optional, Sequence

import httpx

ROOT_DIR = Path(__file__).resolve().parents[1]
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from tools.live_api import DEFAULT_LIVE_ENV_PATH, get_json_from_any, live_api_settings


DEFAULT_OUTPUT_DIR = Path("/private/tmp")


def fetch_report_or_item(
    *,
    report_id: Optional[int] = None,
    item_id: Optional[int] = None,
    base_urls: Sequence[str],
    token: str,
    timeout: float = 30.0,
    client_factory: Any = httpx.Client,
) -> Dict[str, Any]:
    report: Dict[str, Any] = {}
    base_url = ""
    if report_id is not None:
        report_payload, base_url = get_json_from_any(
            base_urls=base_urls,
            token=token,
            path=f"/api/reports/{int(report_id)}",
            timeout=timeout,
            client_factory=client_factory,
        )
        report = _dict_value(report_payload.get("report") or report_payload)
        item_id = int(report.get("item_id") or item_id or 0)

    if not item_id:
        raise ValueError("Provide --report-id or --item-id.")

    item_payload, item_base_url = get_json_from_any(
        base_urls=base_urls,
        token=token,
        path=f"/api/items/{int(item_id)}",
        timeout=timeout,
        client_factory=client_factory,
    )
    item = _dict_value(item_payload.get("item") or item_payload)
    return {
        "base_url": item_base_url,
        "report": report,
        "item": item,
    }


def write_live_payload(payload: Mapping[str, Any], output_dir: Path = DEFAULT_OUTPUT_DIR) -> Path:
    report = _dict_value(payload.get("report"))
    item = _dict_value(payload.get("item"))
    report_id = int(report.get("id") or 0)
    item_id = int(item.get("id") or report.get("item_id") or 0)
    name = f"whackamole-report-{report_id}.json" if report_id else f"whackamole-item-{item_id}.json"
    path = output_dir / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return path


def summary_lines(payload: Mapping[str, Any], output_path: Optional[Path] = None) -> Sequence[str]:
    report = _dict_value(payload.get("report"))
    item = _dict_value(payload.get("item"))
    checks = _dict_value(item.get("checks"))
    media = _dict_value(checks.get("media"))
    decision = _dict_value(checks.get("decision") or item.get("decision"))
    issues = [
        str(issue.get("key") or "")
        for issue in media.get("issues", [])
        if isinstance(issue, Mapping) and str(issue.get("key") or "")
    ]
    resolved = [
        str(issue.get("key") or "")
        for issue in media.get("resolved_mediainfo_issues", [])
        if isinstance(issue, Mapping) and str(issue.get("key") or "")
    ]
    report_prefix = (
        f"Report {int(report.get('id') or 0)} | item {int(report.get('item_id') or item.get('id') or 0)}"
        if report
        else f"Item {int(item.get('id') or 0)}"
    )
    lines = [
        f"{report_prefix} | {str(report.get('stage') or '-')} | {str(report.get('state') or '-')}",
        f"Item | {str(item.get('status') or '-')} / {str(item.get('verdict') or '-')} | {str(item.get('reason') or '-')}",
        (
            "Decision | "
            f"{str(decision.get('winning_rule_id') or decision.get('rule') or '-')} | "
            f"{str(decision.get('status') or '-')} / {str(decision.get('verdict') or '-')}"
        ),
        f"Media issues | {_join_or_none(issues)}",
        f"Resolved issues | {_join_or_none(resolved)}",
    ]
    notes = str(report.get("notes") or "").strip()
    if notes:
        lines.append(f"Report notes | {notes}")
    if output_path:
        lines.append(f"JSON | {output_path}")
    return lines


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Inspect one live Whackamole report or item with compact output.")
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--report-id", type=int, help="Whackamole reporting-system issue id.")
    group.add_argument("--item-id", type=int, help="Whackamole item id.")
    parser.add_argument("--base-url", default="", help="Override WHACKAMOLE_API_BASE_URL.")
    parser.add_argument("--fallback-url", default="", help="Override WHACKAMOLE_API_FALLBACK_URL.")
    parser.add_argument("--token-env", default="WHACKAMOLE_API_TOKEN", help="Environment variable containing the API token.")
    parser.add_argument("--env-file", type=Path, default=DEFAULT_LIVE_ENV_PATH, help="Local env file to load.")
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT_DIR, help="Directory for full JSON output.")
    parser.add_argument("--timeout", type=float, default=30.0, help="HTTP timeout in seconds.")
    return parser


def main(argv: Optional[Sequence[str]] = None) -> int:
    args = build_parser().parse_args(argv)
    settings = live_api_settings(
        base_url=args.base_url,
        fallback_url=args.fallback_url,
        token_env=args.token_env,
        env_path=args.env_file,
    )
    if not settings.token:
        print(f"Missing API token. Set {args.token_env} or {args.env_file}.", file=sys.stderr)
        return 2
    try:
        payload = fetch_report_or_item(
            report_id=args.report_id,
            item_id=args.item_id,
            base_urls=settings.base_urls(),
            token=settings.token,
            timeout=args.timeout,
        )
    except httpx.HTTPStatusError as exc:
        print(f"API request failed: HTTP {exc.response.status_code} for {exc.request.url}", file=sys.stderr)
        return 1
    except Exception as exc:
        print(f"API request failed: {exc}", file=sys.stderr)
        return 1
    output_path = write_live_payload(payload, args.output_dir)
    print("\n".join(summary_lines(payload, output_path)))
    return 0


def _dict_value(value: Any) -> Dict[str, Any]:
    return dict(value) if isinstance(value, Mapping) else {}


def _join_or_none(values: Sequence[str]) -> str:
    return ", ".join(value for value in values if value) or "none"


if __name__ == "__main__":
    raise SystemExit(main())
