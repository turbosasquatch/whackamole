from __future__ import annotations

import json
from pathlib import Path

import httpx
import pytest

from tools.unraid_update_whackamole import (
    EXPECTED_IMAGE,
    REVISION_LABEL,
    build_parser,
    deploy_whackamole,
    inspect_whackamole,
    unraid_settings,
)


API_URL = "http://unraid.test/graphql"
HEALTH_URL = "http://unraid.test:8383/api/status"
EXPECTED_SHA = "a" * 40


def container(
    *,
    container_id: str = "old-id",
    name: str = "/Whackamole",
    image: str = EXPECTED_IMAGE,
    revision: str = "b" * 40,
    state: str = "RUNNING",
    status: str = "Up 5 minutes (healthy)",
) -> dict:
    return {
        "id": container_id,
        "names": [name],
        "image": image,
        "imageId": f"sha256:{container_id}",
        "labels": {REVISION_LABEL: revision},
        "state": state,
        "status": status,
        "isUpdateAvailable": revision != EXPECTED_SHA,
    }


def preflight_payload(items: list[dict], api_version: str = "4.35.1+a9625ae2") -> dict:
    return {
        "data": {
            "info": {"versions": {"core": {"api": api_version}}},
            "docker": {"containers": items},
        }
    }


def clients(graphql_handler, health_handler=None):
    health_handler = health_handler or (lambda request: httpx.Response(200, json={"ok": True}))
    graphql_client = httpx.Client(transport=httpx.MockTransport(graphql_handler))
    health_client = httpx.Client(transport=httpx.MockTransport(health_handler))
    return graphql_client, health_client


def request_json(request: httpx.Request) -> dict:
    return json.loads(request.content.decode("utf-8"))


def test_unraid_settings_loads_ignored_env_without_exposing_key(tmp_path: Path):
    env_path = tmp_path / "unraid-api.env"
    env_path.write_text(
        "UNRAID_API_URL=http://192.168.1.16/graphql\nUNRAID_API_KEY=secret-value\n",
        encoding="utf-8",
    )

    settings = unraid_settings(env_path=env_path, environ={})

    assert settings.api_url == "http://192.168.1.16/graphql"
    assert settings.api_key == "secret-value"


def test_cli_accepts_fallback_health_url():
    args = build_parser().parse_args(["--health-url", HEALTH_URL])

    assert args.health_url == HEALTH_URL


def test_inspect_rejects_old_api_version():
    graphql_client, _ = clients(lambda request: httpx.Response(200, json=preflight_payload([container()], "4.29.2")))

    with pytest.raises(RuntimeError, match="requires 4.30.0"):
        inspect_whackamole(graphql_client, API_URL)


@pytest.mark.parametrize(
    ("items", "message"),
    [
        ([], "found 0"),
        ([container(), container(container_id="duplicate")], "found 2"),
        ([container(image="example.invalid/wrong:latest")], "Refusing update"),
    ],
)
def test_inspect_requires_one_exact_container_and_image(items, message):
    graphql_client, _ = clients(lambda request: httpx.Response(200, json=preflight_payload(items)))

    with pytest.raises(RuntimeError, match=message):
        inspect_whackamole(graphql_client, API_URL)


def test_already_current_is_a_healthy_noop():
    requests = []

    def graphql_handler(request):
        requests.append(request_json(request)["query"])
        return httpx.Response(200, json=preflight_payload([container(revision=EXPECTED_SHA)]))

    graphql_client, health_client = clients(graphql_handler)
    result = deploy_whackamole(
        graphql_client=graphql_client,
        health_client=health_client,
        api_url=API_URL,
        health_url=HEALTH_URL,
        expected_sha=EXPECTED_SHA,
    )

    assert result.action == "already-current"
    assert len(requests) == 1


def test_update_mutation_rediscovers_changed_id_and_verifies_revision():
    query_count = 0
    mutation_ids = []

    def graphql_handler(request):
        nonlocal query_count
        body = request_json(request)
        if "mutation UpdateWhackamole" in body["query"]:
            mutation_ids.append(body["variables"]["id"])
            return httpx.Response(200, json={"data": {"docker": {"updateContainer": container()}}})
        query_count += 1
        current = container() if query_count == 1 else container(container_id="new-id", revision=EXPECTED_SHA)
        return httpx.Response(200, json=preflight_payload([current]))

    graphql_client, health_client = clients(graphql_handler)
    result = deploy_whackamole(
        graphql_client=graphql_client,
        health_client=health_client,
        api_url=API_URL,
        health_url=HEALTH_URL,
        expected_sha=EXPECTED_SHA,
        poll_interval=0,
    )

    assert mutation_ids == ["old-id"]
    assert result.action == "updated"
    assert result.container.id == "new-id"
    assert result.container.revision == EXPECTED_SHA


def test_deployment_times_out_when_revision_never_changes():
    def graphql_handler(request):
        if "mutation UpdateWhackamole" in request_json(request)["query"]:
            return httpx.Response(200, json={"data": {"docker": {"updateContainer": container()}}})
        return httpx.Response(200, json=preflight_payload([container()]))

    graphql_client, health_client = clients(graphql_handler)
    with pytest.raises(RuntimeError, match="Timed out"):
        deploy_whackamole(
            graphql_client=graphql_client,
            health_client=health_client,
            api_url=API_URL,
            health_url=HEALTH_URL,
            expected_sha=EXPECTED_SHA,
            timeout=0,
            poll_interval=0,
        )


def test_graphql_errors_are_compact_and_fail_closed():
    graphql_client, _ = clients(
        lambda request: httpx.Response(200, json={"errors": [{"message": "permission denied"}]})
    )

    with pytest.raises(RuntimeError, match="permission denied"):
        inspect_whackamole(graphql_client, API_URL)


def test_health_failure_fails_an_otherwise_successful_check():
    graphql_client, health_client = clients(
        lambda request: httpx.Response(200, json=preflight_payload([container(revision=EXPECTED_SHA)])),
        lambda request: httpx.Response(503, json={"ok": False}),
    )

    with pytest.raises(httpx.HTTPStatusError):
        deploy_whackamole(
            graphql_client=graphql_client,
            health_client=health_client,
            api_url=API_URL,
            health_url=HEALTH_URL,
            expected_sha=EXPECTED_SHA,
            check_only=True,
        )
