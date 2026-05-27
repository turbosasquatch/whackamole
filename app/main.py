from __future__ import annotations

import json
import os
import time
from hmac import compare_digest
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any, Dict, List, Optional

import httpx
from fastapi import FastAPI, Form, HTTPException, Query, Request
from fastapi.responses import HTMLResponse, JSONResponse, PlainTextResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from starlette import status

from app.clients import QuiClient, RadarrClient, SonarrClient, UploadAssistantClient
from app.config import (
    AppConfig,
    ConfigManager,
    SecretStore,
    format_path_mappings,
    join_csv,
    parse_csv,
    parse_path_mappings,
)
from app.database import Database
from app.reducer import TRACKER_BUCKETS
from app.service import WhackamoleService

APP_DIR = Path(__file__).resolve().parent
templates = Jinja2Templates(directory=str(APP_DIR / "templates"))


def _format_datetime(value: Optional[int]) -> str:
    if not value:
        return "-"
    return time.strftime("%Y-%m-%d %H:%M", time.localtime(int(value)))


def _format_bytes(value: Optional[int]) -> str:
    amount = float(value or 0)
    units = ["B", "KiB", "MiB", "GiB", "TiB"]
    for unit in units:
        if amount < 1024 or unit == units[-1]:
            return f"{amount:.1f} {unit}" if unit != "B" else f"{int(amount)} B"
        amount /= 1024
    return f"{amount:.1f} TiB"


templates.env.filters["datetime"] = _format_datetime
templates.env.filters["bytes"] = _format_bytes


def _config_dir() -> str:
    return os.getenv("WHACKAMOLE_CONFIG_DIR", "/config")


def _row_dict(row: Any) -> Dict[str, Any]:
    item = dict(row)
    tracker_groups = _tracker_result_groups(item.get("tracker_results"), item.get("verdict"))
    arr_result = _arr_result(item.get("arr_results"))
    item["tracker_results"] = tracker_groups
    item["tracker_buckets"] = _tracker_bucket_items(tracker_groups)
    item["tracker_summary"] = _tracker_summary(tracker_groups)
    item["arr_result"] = arr_result
    item["arr_summary"] = _arr_summary(arr_result)
    return item


def _json_object(value: Any) -> Dict[str, Any]:
    try:
        parsed = json.loads(value or "{}")
    except (TypeError, json.JSONDecodeError):
        return {}
    return parsed if isinstance(parsed, dict) else {}


def _api_item_summary(row: Any) -> Dict[str, Any]:
    item = _row_dict(row)
    return {
        "id": item["id"],
        "instance_id": item["instance_id"],
        "hash": item["hash"],
        "name": item["name"],
        "category": item["category"],
        "tags": item["tags"],
        "content_path": item["content_path"],
        "mapped_path": item["mapped_path"],
        "status": item["status"],
        "verdict": item["verdict"],
        "reason": item["reason"],
        "size": item["size"],
        "added_on": item["added_on"],
        "completion_on": item["completion_on"],
        "discovered_at": item["discovered_at"],
        "updated_at": item["updated_at"],
        "last_checked_at": item["last_checked_at"],
        "next_check_at": item["next_check_at"],
        "attempt_count": item["attempt_count"],
        "baseline": bool(item["baseline"]),
        "ignored_reason": item["ignored_reason"],
        "tracker_results": item["tracker_results"],
        "tracker_summary": item["tracker_summary"],
        "arr_summary": item["arr_summary"],
    }


def _api_item_detail(row: Any) -> Dict[str, Any]:
    item = _row_dict(row)
    summary = _api_item_summary(row)
    raw_torrent = _json_object(item.get("raw_torrent"))
    ua = {
        "session_id": item["ua_session_id"],
        "args": item["ua_args"],
        "log": item["ua_log"],
        "tracker_results": item["tracker_results"],
        "tracker_summary": item["tracker_summary"],
    }
    arr = item["arr_result"]
    summary.update(
        {
            "raw_torrent": raw_torrent,
            "ua": ua,
            "arr": arr,
            "checks": {
                "ua": ua,
                "arr": arr,
            },
        }
    )
    return summary


def _tracker_result_groups(value: Any, verdict: Any = "") -> Dict[str, List[str]]:
    groups: Dict[str, List[str]] = {bucket: [] for bucket in TRACKER_BUCKETS}
    try:
        parsed = json.loads(value or "[]")
    except (TypeError, json.JSONDecodeError):
        parsed = []

    if isinstance(parsed, dict):
        raw_groups = parsed.get("groups") if isinstance(parsed.get("groups"), dict) else parsed
        for bucket in TRACKER_BUCKETS:
            values = raw_groups.get(bucket, [])
            if isinstance(values, list):
                groups[bucket] = [str(item) for item in values if str(item).strip()]
        return groups

    if isinstance(parsed, list):
        legacy_bucket = _legacy_tracker_bucket(str(verdict or ""))
        groups[legacy_bucket] = [str(item) for item in parsed if str(item).strip()]
    return groups


def _legacy_tracker_bucket(verdict: str) -> str:
    if verdict == "dupe":
        return "dupe"
    if verdict == "skipped":
        return "skipped"
    if verdict in {"error", "http_error", "ua_error", "path_mapping"}:
        return "error"
    return "passed"


def _tracker_bucket_items(groups: Dict[str, List[str]]) -> Dict[str, List[Dict[str, Any]]]:
    return {
        bucket: [
            {
                "name": tracker,
            }
            for tracker in groups.get(bucket, [])
        ]
        for bucket in TRACKER_BUCKETS
    }


def _tracker_summary(groups: Dict[str, List[str]]) -> str:
    labels = {
        "passed": "Missing/upload-worthy",
        "dupe": "Dupes",
        "skipped": "Skipped",
        "error": "Errors",
    }
    parts = [
        f"{labels[bucket]}: {', '.join(groups[bucket])}"
        for bucket in TRACKER_BUCKETS
        if groups.get(bucket)
    ]
    return " | ".join(parts)


def _arr_result(value: Any) -> Dict[str, Any]:
    return _json_object(value)


def _arr_summary(result: Dict[str, Any]) -> str:
    decisions = result.get("decisions")
    if not isinstance(decisions, list) or not decisions:
        return ""
    valid = [str(item.get("tracker")) for item in decisions if item.get("status") == "candidate"]
    blocked = [str(item.get("tracker")) for item in decisions if item.get("status") == "blocked"]
    manual = [str(item.get("tracker")) for item in decisions if item.get("status") == "manual_review"]
    parts = []
    if valid:
        parts.append(f"Valid: {', '.join(valid)}")
    if blocked:
        parts.append(f"Equal/better exists: {', '.join(blocked)}")
    if manual:
        parts.append(f"Manual review: {', '.join(manual)}")
    return " | ".join(parts)


def _as_int(value: Any, default: int, minimum: Optional[int] = None) -> int:
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        parsed = default
    if minimum is not None:
        parsed = max(minimum, parsed)
    return parsed


def _secret_state(secrets: SecretStore) -> Dict[str, bool]:
    return {
        "whackamole_api_token": secrets.has("whackamole_api_token"),
        "qui_api_key": secrets.has("qui_api_key"),
        "ua_bearer_token": secrets.has("ua_bearer_token"),
        "sonarr_api_key": secrets.has("sonarr_api_key"),
        "radarr_api_key": secrets.has("radarr_api_key"),
        "easycross_api_key": secrets.has("easycross_api_key"),
    }


def _config_context(request: Request, message: str = "", probe_results: Optional[List[Dict[str, str]]] = None) -> Dict[str, Any]:
    cfg = request.app.state.config_manager.load()
    secrets = request.app.state.secrets
    return {
        "request": request,
        "cfg": cfg,
        "secrets": _secret_state(secrets),
        "path_mappings": format_path_mappings(cfg.path_mappings),
        "exclude_category_terms": join_csv(cfg.watch.exclude_category_terms),
        "exclude_tag_terms": join_csv(cfg.watch.exclude_tag_terms),
        "error_backoff_minutes": join_csv([str(item) for item in cfg.safety.error_backoff_minutes]),
        "message": message,
        "probe_results": probe_results or [],
    }


@asynccontextmanager
async def lifespan(app: FastAPI):
    app.state.config_manager = ConfigManager(_config_dir())
    app.state.secrets = SecretStore(_config_dir())
    app.state.db = Database(str(Path(_config_dir()) / "whackamole.db"))
    app.state.service = WhackamoleService(app.state.config_manager, app.state.secrets, app.state.db)
    app.state.service.start()
    try:
        yield
    finally:
        await app.state.service.stop()


app = FastAPI(title="Whackamole", lifespan=lifespan)
app.mount("/static", StaticFiles(directory=str(APP_DIR / "static")), name="static")


@app.get("/", response_class=HTMLResponse)
async def dashboard(request: Request, view: str = "active") -> HTMLResponse:
    groups = {
        "active": ["queued", "deferred", "checking", "error"],
        "candidates": ["candidate"],
        "blocked": ["blocked"],
        "manual": ["manual_review"],
        "baseline": ["baseline"],
        "ignored": ["ignored"],
        "all": [],
    }
    selected = view if view in groups else "active"
    rows = request.app.state.db.list_items(groups[selected], limit=150)
    context = {
        "request": request,
        "items": [_row_dict(row) for row in rows],
        "view": selected,
        "counts": request.app.state.db.status_counts(),
        "service": request.app.state.service.snapshot(),
    }
    return templates.TemplateResponse("dashboard.html", context)


@app.get("/items/{item_id}", response_class=HTMLResponse)
async def item_detail(request: Request, item_id: int) -> HTMLResponse:
    row = request.app.state.db.get_item(item_id)
    if row is None:
        return templates.TemplateResponse(
            "item.html",
            {"request": request, "item": None, "service": request.app.state.service.snapshot()},
            status_code=404,
        )
    return templates.TemplateResponse(
        "item.html",
        {"request": request, "item": _row_dict(row), "service": request.app.state.service.snapshot()},
    )


@app.post("/items/{item_id}/recheck")
async def recheck_item(item_id: int) -> RedirectResponse:
    app.state.db.requeue(item_id)
    return RedirectResponse(url=f"/items/{item_id}", status_code=status.HTTP_303_SEE_OTHER)


@app.post("/items/{item_id}/ignore")
async def ignore_item(item_id: int) -> RedirectResponse:
    app.state.db.ignore(item_id)
    return RedirectResponse(url="/", status_code=status.HTTP_303_SEE_OTHER)


@app.get("/config", response_class=HTMLResponse)
async def config_page(request: Request) -> HTMLResponse:
    return templates.TemplateResponse("config.html", _config_context(request))


@app.post("/config", response_class=HTMLResponse)
async def save_config(
    request: Request,
    qui_url: str = Form(""),
    qui_instance_id: str = Form("1"),
    qui_page_limit: str = Form("200"),
    qui_api_key: str = Form(""),
    clear_qui_api_key: Optional[str] = Form(None),
    ua_url: str = Form(""),
    ua_tmp_path: str = Form("/ua-tmp"),
    ua_timeout: str = Form("3600"),
    ua_bearer_token: str = Form(""),
    clear_ua_bearer_token: Optional[str] = Form(None),
    path_mappings: str = Form(""),
    exclude_category_terms: str = Form(""),
    exclude_tag_terms: str = Form(""),
    process_existing_on_first_run: Optional[str] = Form(None),
    poll_interval_seconds: str = Form("60"),
    max_queue_size: str = Form("250"),
    max_concurrent_ua_jobs: str = Form("1"),
    min_seconds_between_ua_jobs: str = Form("120"),
    arr_search_timeout_seconds: str = Form("45"),
    recheck_cooldown_hours: str = Form("24"),
    max_error_retries: str = Form("3"),
    error_backoff_minutes: str = Form("15, 60, 360"),
    sonarr_url: str = Form(""),
    sonarr_api_key: str = Form(""),
    clear_sonarr_api_key: Optional[str] = Form(None),
    radarr_url: str = Form(""),
    radarr_api_key: str = Form(""),
    clear_radarr_api_key: Optional[str] = Form(None),
    easycross_url: str = Form(""),
    easycross_api_key: str = Form(""),
    clear_easycross_api_key: Optional[str] = Form(None),
    whackamole_api_token: str = Form(""),
    clear_whackamole_api_token: Optional[str] = Form(None),
) -> HTMLResponse:
    manager: ConfigManager = request.app.state.config_manager
    secrets: SecretStore = request.app.state.secrets
    cfg: AppConfig = manager.load()

    cfg.qui.url = qui_url.strip().rstrip("/")
    cfg.qui.instance_id = _as_int(qui_instance_id, cfg.qui.instance_id, minimum=1)
    cfg.qui.page_limit = _as_int(qui_page_limit, cfg.qui.page_limit, minimum=1)
    cfg.upload_assistant.url = ua_url.strip().rstrip("/")
    cfg.upload_assistant.tmp_path = ua_tmp_path.strip() or "/ua-tmp"
    cfg.upload_assistant.request_timeout_seconds = _as_int(ua_timeout, cfg.upload_assistant.request_timeout_seconds, minimum=60)
    cfg.path_mappings = parse_path_mappings(path_mappings)

    cfg.watch.exclude_category_terms = parse_csv(exclude_category_terms)
    cfg.watch.exclude_tag_terms = parse_csv(exclude_tag_terms)
    cfg.watch.process_existing_on_first_run = process_existing_on_first_run == "on"

    cfg.safety.poll_interval_seconds = _as_int(poll_interval_seconds, cfg.safety.poll_interval_seconds, minimum=15)
    cfg.safety.max_queue_size = _as_int(max_queue_size, cfg.safety.max_queue_size, minimum=1)
    cfg.safety.max_concurrent_ua_jobs = _as_int(max_concurrent_ua_jobs, cfg.safety.max_concurrent_ua_jobs, minimum=1)
    cfg.safety.min_seconds_between_ua_jobs = _as_int(
        min_seconds_between_ua_jobs,
        cfg.safety.min_seconds_between_ua_jobs,
        minimum=0,
    )
    cfg.safety.arr_search_timeout_seconds = _as_int(
        arr_search_timeout_seconds,
        cfg.safety.arr_search_timeout_seconds,
        minimum=5,
    )
    cfg.safety.recheck_cooldown_hours = _as_int(recheck_cooldown_hours, cfg.safety.recheck_cooldown_hours, minimum=1)
    cfg.safety.max_error_retries = _as_int(max_error_retries, cfg.safety.max_error_retries, minimum=0)
    cfg.safety.error_backoff_minutes = [
        _as_int(item, 15, minimum=1)
        for item in parse_csv(error_backoff_minutes)
    ] or [15, 60, 360]

    cfg.sonarr.url = sonarr_url.strip().rstrip("/")
    cfg.radarr.url = radarr_url.strip().rstrip("/")
    cfg.easycross.url = easycross_url.strip().rstrip("/")

    _update_secret(secrets, "qui_api_key", qui_api_key, clear_qui_api_key)
    _update_secret(secrets, "ua_bearer_token", ua_bearer_token, clear_ua_bearer_token)
    _update_secret(secrets, "sonarr_api_key", sonarr_api_key, clear_sonarr_api_key)
    _update_secret(secrets, "radarr_api_key", radarr_api_key, clear_radarr_api_key)
    _update_secret(secrets, "easycross_api_key", easycross_api_key, clear_easycross_api_key)
    _update_secret(secrets, "whackamole_api_token", whackamole_api_token, clear_whackamole_api_token)

    manager.save(cfg)
    return templates.TemplateResponse("config.html", _config_context(request, message="Settings saved."))


@app.post("/config/probe", response_class=HTMLResponse)
async def probe_config(request: Request) -> HTMLResponse:
    cfg = request.app.state.config_manager.load()
    secrets = request.app.state.secrets
    results: List[Dict[str, str]] = []

    if cfg.qui.url:
        try:
            client = QuiClient(cfg, secrets.get("qui_api_key"))
            await client.health()
            instances = await client.list_instances() if secrets.has("qui_api_key") else []
            detail = f"Connected. {len(instances)} instance(s) visible." if instances else "Setup endpoint reachable."
            results.append({"name": "QUI", "state": "ok", "detail": detail})
        except Exception as exc:
            results.append({"name": "QUI", "state": "error", "detail": _short_error(exc)})

    if cfg.upload_assistant.url:
        try:
            client = UploadAssistantClient(cfg, secrets.get("ua_bearer_token"))
            await client.health()
            roots = await client.browse_roots() if secrets.has("ua_bearer_token") else {}
            detail = "Connected."
            if isinstance(roots, dict) and roots:
                detail = f"Connected. Browse roots: {', '.join(str(k) for k in roots.keys())}."
            results.append({"name": "Upload Assistant", "state": "ok", "detail": detail})
        except Exception as exc:
            results.append({"name": "Upload Assistant", "state": "error", "detail": _short_error(exc)})

    if cfg.sonarr.url:
        try:
            client = SonarrClient(cfg.sonarr.url, secrets.get("sonarr_api_key"), cfg.safety.arr_search_timeout_seconds)
            status_payload = await client.system_status()
            indexers = await client.list_indexers() if secrets.has("sonarr_api_key") else []
            torrent_count = sum(1 for indexer in indexers if str(indexer.get("protocol", "")).lower() == "torrent")
            detail = f"Connected to {status_payload.get('appName', 'Sonarr')}. {torrent_count} torrent indexer(s)."
            results.append({"name": "Sonarr", "state": "ok", "detail": detail})
        except Exception as exc:
            results.append({"name": "Sonarr", "state": "error", "detail": _short_error(exc)})

    if cfg.radarr.url:
        try:
            client = RadarrClient(cfg.radarr.url, secrets.get("radarr_api_key"), cfg.safety.arr_search_timeout_seconds)
            status_payload = await client.system_status()
            indexers = await client.list_indexers() if secrets.has("radarr_api_key") else []
            torrent_count = sum(1 for indexer in indexers if str(indexer.get("protocol", "")).lower() == "torrent")
            detail = f"Connected to {status_payload.get('appName', 'Radarr')}. {torrent_count} torrent indexer(s)."
            results.append({"name": "Radarr", "state": "ok", "detail": detail})
        except Exception as exc:
            results.append({"name": "Radarr", "state": "error", "detail": _short_error(exc)})

    if not results:
        results.append({"name": "Configuration", "state": "idle", "detail": "Add URLs and saved keys before probing."})

    return templates.TemplateResponse("config.html", _config_context(request, probe_results=results))


@app.get("/api/status")
async def api_status(request: Request) -> JSONResponse:
    return JSONResponse(
        {
            "service": request.app.state.service.snapshot(),
            "counts": request.app.state.db.status_counts(),
            "configured": _secret_state(request.app.state.secrets),
        }
    )


@app.get("/api/items")
async def api_items(
    request: Request,
    status_filter: Optional[str] = Query(None, alias="status"),
    limit: int = Query(100, ge=1, le=500),
    offset: int = Query(0, ge=0),
    include_details: bool = Query(False),
) -> JSONResponse:
    _require_api_auth(request)
    statuses = _parse_status_filter(status_filter)
    rows = request.app.state.db.list_items(statuses, limit=limit, offset=offset)
    serializer = _api_item_detail if include_details else _api_item_summary
    return JSONResponse(
        {
            "items": [serializer(row) for row in rows],
            "count": len(rows),
            "total": request.app.state.db.count_items(statuses),
            "limit": limit,
            "offset": offset,
            "status": statuses,
            "include_details": include_details,
        }
    )


@app.get("/api/items/{item_id}")
async def api_item_detail(request: Request, item_id: int) -> JSONResponse:
    _require_api_auth(request)
    row = request.app.state.db.get_item(item_id)
    if row is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Item not found")
    return JSONResponse(_api_item_detail(row))


@app.get("/api/items/{item_id}/log")
async def api_item_log(request: Request, item_id: int) -> PlainTextResponse:
    _require_api_auth(request)
    row = request.app.state.db.get_item(item_id)
    if row is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Item not found")
    return PlainTextResponse(str(row["ua_log"] or ""), media_type="text/plain")


def _update_secret(secrets: SecretStore, name: str, value: str, clear: Optional[str]) -> None:
    if clear == "on":
        secrets.clear(name)
    elif value.strip():
        secrets.set(name, value.strip())


def _short_error(exc: Exception) -> str:
    if isinstance(exc, httpx.HTTPStatusError):
        return f"HTTP {exc.response.status_code}"
    return str(exc)[:240]


def _require_api_auth(request: Request) -> None:
    expected = request.app.state.secrets.get("whackamole_api_token")
    if not expected:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Whackamole API token is not configured",
            headers={"WWW-Authenticate": "Bearer"},
        )
    authorization = request.headers.get("authorization", "")
    scheme, _, token = authorization.partition(" ")
    if scheme.lower() != "bearer" or not token or not compare_digest(token, expected):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid API token",
            headers={"WWW-Authenticate": "Bearer"},
        )


def _parse_status_filter(value: Optional[str]) -> List[str]:
    if not value:
        return []
    return [part.strip() for part in value.split(",") if part.strip()]
