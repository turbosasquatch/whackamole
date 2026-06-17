from __future__ import annotations

import argparse
import json
import os
import sys
import time
from collections import Counter, defaultdict
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, Iterable, List, Mapping, Optional, Sequence

import httpx

ROOT_DIR = Path(__file__).resolve().parents[1]
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from app.rules import RULESET_VERSION, evaluate_decision


DEFAULT_BASE_URL = "http://100.72.6.46:9393"
DEFAULT_OUTPUT = "/private/tmp/whackamole-rule-replay.json"


@dataclass(frozen=True)
class ReplaySource:
    item_id: int
    reports: List[Dict[str, Any]]


def fetch_reported_items(
    *,
    base_url: str,
    token: str,
    report_state: str = "open",
    report_limit: int = 500,
    item_ids: Optional[Iterable[int]] = None,
    timeout: float = 30.0,
    client_factory: Any = httpx.Client,
) -> Dict[str, Any]:
    base_url = base_url.rstrip("/")
    report_limit = min(max(1, int(report_limit)), 500)
    explicit_ids = [int(value) for value in (item_ids or [])]

    with client_factory(base_url=base_url, timeout=timeout, headers=_auth_headers(token)) as client:
        reports = []
        seen_report_ids = set()
        for state in _report_states(report_state):
            reports_payload = _get_json(client, "/api/reports", params={"state": state, "limit": report_limit})
            for report in reports_payload.get("reports", []):
                if not isinstance(report, Mapping):
                    continue
                report_id = int(report.get("id") or 0)
                if report_id and report_id in seen_report_ids:
                    continue
                if report_id:
                    seen_report_ids.add(report_id)
                reports.append(dict(report))
        sources = _replay_sources(reports, explicit_ids)

        items: List[Dict[str, Any]] = []
        for source in sources:
            try:
                payload = _get_json(client, f"/api/items/{source.item_id}")
            except Exception as exc:
                items.append(_fetch_error_payload(source, exc))
                continue
            items.append(replay_item_payload(payload, source.reports))

    return replay_report(
        base_url=base_url,
        report_state=report_state,
        reports=reports,
        items=items,
        explicit_item_ids=explicit_ids,
    )


def replay_item_payload(item: Mapping[str, Any], reports: Sequence[Mapping[str, Any]]) -> Dict[str, Any]:
    checks = _dict_value(item.get("checks"))
    arr = _dict_value(item.get("arr")) or _dict_value(checks.get("arr"))
    tracker_results = _tracker_results(item, checks)
    current = {
        "status": str(item.get("status") or ""),
        "verdict": str(item.get("verdict") or ""),
        "reason": str(item.get("reason") or ""),
    }
    decision = evaluate_decision(
        item_name=str(item.get("name") or ""),
        current_status=current["status"],
        current_verdict=current["verdict"],
        current_reason=current["reason"],
        tracker_results=tracker_results,
        arr_results=arr,
        check_results=checks,
    )
    stored_decision = checks.get("decision") if isinstance(checks.get("decision"), Mapping) else {}
    top_level_changed = (
        current["status"] != decision.status
        or current["verdict"] != decision.verdict
        or current["reason"] != decision.reason
    )
    payload_changed = (
        dict(stored_decision) != decision.to_dict()
        or checks.get("rules") != decision.rules_payload()
        or checks.get("ruleset_version") != RULESET_VERSION
    )
    return {
        "item_id": int(item.get("id") or 0),
        "name": str(item.get("name") or ""),
        "hash": str(item.get("hash") or ""),
        "reports": [_report_summary(report) for report in reports],
        "current": current,
        "replayed": {
            "status": decision.status,
            "verdict": decision.verdict,
            "reason": decision.reason,
            "rule": decision.winning_rule_id,
            "replayable": decision.replayable,
            "retryable": decision.retryable,
        },
        "outcome": _outcome(current["status"], decision.status, top_level_changed, payload_changed, decision.replayable),
        "status_movement": f"{current['status']} -> {decision.status}" if current["status"] != decision.status else "",
        "metadata_changed": bool(payload_changed and not top_level_changed),
        "rules": decision.rules_payload(),
    }


def replay_report(
    *,
    base_url: str,
    report_state: str,
    reports: Sequence[Mapping[str, Any]],
    items: Sequence[Mapping[str, Any]],
    explicit_item_ids: Sequence[int] = (),
) -> Dict[str, Any]:
    movements = Counter(str(item.get("status_movement") or "") for item in items)
    movements.pop("", None)
    outcomes = Counter(str(item.get("outcome") or "unknown") for item in items)
    return {
        "generated_at": int(time.time()),
        "base_url": base_url,
        "report_state": report_state,
        "ruleset_version": RULESET_VERSION,
        "report_count": len(reports),
        "item_count": len(items),
        "explicit_item_ids": list(explicit_item_ids),
        "summary": {
            "outcomes": dict(sorted(outcomes.items())),
            "movements": dict(sorted(movements.items())),
            "fetch_errors": outcomes.get("fetch_error", 0),
        },
        "items": list(items),
    }


def write_report(report: Mapping[str, Any], output_path: str) -> Path:
    path = Path(output_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return path


def print_summary(report: Mapping[str, Any], output_path: Path) -> None:
    summary = _dict_value(report.get("summary"))
    outcomes = _dict_value(summary.get("outcomes"))
    movements = _dict_value(summary.get("movements"))
    print(f"Reports: {report.get('report_count', 0)}")
    print(f"Items: {report.get('item_count', 0)}")
    if outcomes:
        print("Outcomes:")
        for key, count in outcomes.items():
            print(f"  {key}: {count}")
    if movements:
        print("Movements:")
        for key, count in movements.items():
            print(f"  {key}: {count}")
    print(f"Report written: {output_path}")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Replay live Whackamole report items against local rule code.")
    parser.add_argument("--base-url", default=DEFAULT_BASE_URL, help=f"Live Whackamole base URL. Default: {DEFAULT_BASE_URL}")
    parser.add_argument("--token-env", default="WHACKAMOLE_API_TOKEN", help="Environment variable containing the API token.")
    parser.add_argument("--report-state", default="open", help="Report state to pull. 'open' fetches active and attempted. Default: open")
    parser.add_argument("--report-limit", type=int, default=500, help="Maximum reports to pull, capped at 500.")
    parser.add_argument("--item-id", action="append", type=int, default=[], help="Extra item id to fetch even if it has no report.")
    parser.add_argument("--output", default=DEFAULT_OUTPUT, help=f"JSON report path. Default: {DEFAULT_OUTPUT}")
    parser.add_argument("--timeout", type=float, default=30.0, help="HTTP timeout in seconds.")
    return parser


def main(argv: Optional[Sequence[str]] = None) -> int:
    args = build_parser().parse_args(argv)
    token = os.getenv(args.token_env, "").strip()
    if not token:
        print(f"Missing API token. Set {args.token_env}.", file=sys.stderr)
        return 2

    try:
        report = fetch_reported_items(
            base_url=args.base_url,
            token=token,
            report_state=args.report_state,
            report_limit=args.report_limit,
            item_ids=args.item_id,
            timeout=args.timeout,
        )
    except httpx.HTTPStatusError as exc:
        print(f"API request failed: HTTP {exc.response.status_code} for {exc.request.url}", file=sys.stderr)
        return 1
    except httpx.HTTPError as exc:
        print(f"API request failed: {exc}", file=sys.stderr)
        return 1

    output_path = write_report(report, args.output)
    print_summary(report, output_path)
    return 1 if int(_dict_value(report.get("summary")).get("fetch_errors") or 0) else 0


def _auth_headers(token: str) -> Dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


def _get_json(client: httpx.Client, path: str, params: Optional[Mapping[str, Any]] = None) -> Dict[str, Any]:
    response = client.get(path, params=params)
    response.raise_for_status()
    payload = response.json()
    if not isinstance(payload, dict):
        raise ValueError(f"Expected JSON object from {path}")
    return payload


def _report_states(report_state: str) -> List[str]:
    state = str(report_state or "open").strip().lower()
    if state == "open":
        return ["active", "attempted"]
    return [state]


def _replay_sources(reports: Sequence[Mapping[str, Any]], explicit_ids: Sequence[int]) -> List[ReplaySource]:
    grouped: Dict[int, List[Dict[str, Any]]] = defaultdict(list)
    for report in reports:
        item_id = int(report.get("item_id") or 0)
        if item_id:
            grouped[item_id].append(dict(report))
    for item_id in explicit_ids:
        grouped.setdefault(int(item_id), [])
    return [ReplaySource(item_id=item_id, reports=grouped[item_id]) for item_id in sorted(grouped)]


def _tracker_results(item: Mapping[str, Any], checks: Mapping[str, Any]) -> Any:
    if item.get("tracker_results"):
        return item.get("tracker_results")
    ua = checks.get("ua") if isinstance(checks.get("ua"), Mapping) else {}
    return ua.get("tracker_results") if isinstance(ua, Mapping) else {}


def _outcome(
    current_status: str,
    replayed_status: str,
    top_level_changed: bool,
    payload_changed: bool,
    replayable: bool,
) -> str:
    if not replayable:
        return "not_replayable"
    if current_status != replayed_status:
        return "status_changed"
    if top_level_changed:
        return "decision_changed"
    if payload_changed:
        return "metadata_changed"
    if current_status:
        return "unchanged"
    return "unknown"


def _report_summary(report: Mapping[str, Any]) -> Dict[str, Any]:
    return {
        "id": int(report.get("id") or 0),
        "stage": str(report.get("stage") or ""),
        "notes": str(report.get("notes") or ""),
        "state": str(report.get("state") or ""),
        "created_at": int(report.get("created_at") or 0),
        "updated_at": int(report.get("updated_at") or 0),
    }


def _fetch_error_payload(source: ReplaySource, exc: Exception) -> Dict[str, Any]:
    return {
        "item_id": source.item_id,
        "name": "",
        "reports": [_report_summary(report) for report in source.reports],
        "current": {},
        "replayed": {},
        "outcome": "fetch_error",
        "status_movement": "",
        "metadata_changed": False,
        "rules": [],
        "error": str(exc),
    }


def _dict_value(value: Any) -> Dict[str, Any]:
    return dict(value) if isinstance(value, Mapping) else {}


if __name__ == "__main__":
    raise SystemExit(main())
