import json

import httpx

from tools.docker_workflow_status import find_workflow_run, format_run_summary, wait_for_workflow_run
from tools.live_api import live_api_settings, load_env_file
from tools.live_report import fetch_report_or_item, summary_lines, write_live_payload


def test_live_api_settings_loads_env_file_without_printing_values(tmp_path):
    env_file = tmp_path / "live-api.env"
    env_file.write_text(
        "\n".join(
            [
                "WHACKAMOLE_API_BASE_URL='http://primary.test'",
                'WHACKAMOLE_API_FALLBACK_URL="http://fallback.test"',
                "WHACKAMOLE_API_TOKEN=secret-token",
            ]
        ),
        encoding="utf-8",
    )

    values = load_env_file(env_file)
    settings = live_api_settings(env_path=env_file, environ={})

    assert values["WHACKAMOLE_API_TOKEN"] == "secret-token"
    assert settings.base_urls() == ["http://primary.test", "http://fallback.test"]
    assert settings.token == "secret-token"


def test_live_report_fetches_report_item_and_prints_compact_summary(tmp_path):
    base_url = "http://whackamole.test"
    seen = []

    def handler(request):
        seen.append((request.method, str(request.url), request.headers.get("authorization")))
        if str(request.url) == f"{base_url}/api/reports/68":
            return httpx.Response(
                200,
                json={
                    "report": {
                        "id": 68,
                        "item_id": 2450,
                        "stage": "MediaInfo",
                        "notes": "False DV review",
                        "state": "active",
                    }
                },
            )
        if str(request.url) == f"{base_url}/api/items/2450":
            return httpx.Response(
                200,
                json={
                    "id": 2450,
                    "name": "Movie.2160p.DV-GRP",
                    "status": "manual_review",
                    "verdict": "dolby_vision_missing",
                    "reason": "Name says Dolby Vision, but MediaInfo has no Dolby Vision metadata.",
                    "checks": {
                        "decision": {
                            "winning_rule_id": "review.evidence_warning",
                            "status": "manual_review",
                            "verdict": "dolby_vision_missing",
                        },
                        "media": {
                            "issues": [{"key": "dolby_vision_missing"}],
                            "resolved_mediainfo_issues": [{"key": "audio_object_missing"}],
                        },
                    },
                },
            )
        return httpx.Response(404, json={"detail": "not mocked"})

    transport = httpx.MockTransport(handler)
    payload = fetch_report_or_item(
        report_id=68,
        base_urls=[base_url],
        token="token",
        client_factory=lambda **kwargs: httpx.Client(transport=transport, **kwargs),
    )
    output_path = write_live_payload(payload, tmp_path)
    lines = list(summary_lines(payload, output_path))

    assert seen == [
        ("GET", f"{base_url}/api/reports/68", "Bearer token"),
        ("GET", f"{base_url}/api/items/2450", "Bearer token"),
    ]
    assert lines[0] == "Report 68 | item 2450 | MediaInfo | active"
    assert "review.evidence_warning" in lines[2]
    assert "dolby_vision_missing" in lines[3]
    assert "audio_object_missing" in lines[4]
    assert output_path.name == "whackamole-report-68.json"
    assert json.loads(output_path.read_text(encoding="utf-8"))["item"]["id"] == 2450


def test_docker_workflow_status_matches_and_polls_current_sha():
    api_url = "https://api.github.test"
    repo = "owner/repo"
    sha = "abc123456789"

    def handler(request):
        if str(request.url) == f"{api_url}/repos/{repo}/actions/runs?branch=main&event=push&per_page=10":
            return httpx.Response(
                200,
                json={
                    "workflow_runs": [
                        {
                            "id": 123,
                            "name": "Docker Image",
                            "head_sha": sha,
                            "status": "in_progress",
                            "conclusion": None,
                            "html_url": "https://github.test/runs/123",
                        }
                    ]
                },
            )
        if str(request.url) == f"{api_url}/repos/{repo}/actions/runs/123":
            return httpx.Response(
                200,
                json={
                    "id": 123,
                    "name": "Docker Image",
                    "head_sha": sha,
                    "status": "completed",
                    "conclusion": "success",
                    "html_url": "https://github.test/runs/123",
                },
            )
        return httpx.Response(404, json={"detail": "not mocked"})

    transport = httpx.MockTransport(handler)
    client_factory = lambda **kwargs: httpx.Client(transport=transport, **kwargs)

    run = find_workflow_run(
        repo=repo,
        branch="main",
        sha=sha,
        api_url=api_url,
        client_factory=client_factory,
    )
    completed = wait_for_workflow_run(
        repo=repo,
        run=run,
        api_url=api_url,
        poll_interval=0,
        client_factory=client_factory,
    )

    assert completed["conclusion"] == "success"
    assert format_run_summary(completed) == "123 | Docker Image | abc1234 | completed | success | https://github.test/runs/123"
