from __future__ import annotations

import argparse
import os
import re
import subprocess
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping, Optional, Sequence

import httpx

ROOT_DIR = Path(__file__).resolve().parents[1]
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from tools.live_api import load_env_file


DEFAULT_ENV_PATH = ROOT_DIR / ".codex" / "unraid-api.env"
DEFAULT_HEALTH_URL = "http://192.168.1.16:9393/api/status"
EXPECTED_CONTAINER_NAME = "/Whackamole"
EXPECTED_IMAGE = "ghcr.io/turbosasquatch/whackamole:latest"
MINIMUM_API_VERSION = (4, 30, 0)
REVISION_LABEL = "org.opencontainers.image.revision"

PREFLIGHT_QUERY = """
query WhackamoleDeploymentPreflight {
  info { versions { core { api } } }
  docker {
    containers {
      id
      names
      image
      imageId
      labels
      state
      status
      isUpdateAvailable
    }
  }
}
"""

UPDATE_MUTATION = """
mutation UpdateWhackamole($id: PrefixedID!) {
  docker {
    updateContainer(id: $id) {
      id
      names
      image
      imageId
      labels
      state
      status
      isUpdateAvailable
    }
  }
}
"""


@dataclass(frozen=True)
class UnraidSettings:
    api_url: str
    api_key: str


@dataclass(frozen=True)
class ContainerSnapshot:
    id: str
    names: tuple[str, ...]
    image: str
    image_id: str
    revision: str
    state: str
    status: str
    update_available: Optional[bool]

    @property
    def healthy(self) -> bool:
        return self.state == "RUNNING" and "(healthy)" in self.status.lower()


@dataclass(frozen=True)
class DeploymentResult:
    action: str
    api_version: str
    container: ContainerSnapshot


def unraid_settings(
    *,
    env_path: Path = DEFAULT_ENV_PATH,
    environ: Optional[Mapping[str, str]] = None,
) -> UnraidSettings:
    env = dict(os.environ if environ is None else environ)
    file_env = load_env_file(env_path)
    api_url = str(env.get("UNRAID_API_URL") or file_env.get("UNRAID_API_URL") or "").rstrip("/")
    api_key = str(env.get("UNRAID_API_KEY") or file_env.get("UNRAID_API_KEY") or "")
    if not api_url:
        raise ValueError(f"UNRAID_API_URL is not configured in {env_path}")
    if not api_key:
        raise ValueError(f"UNRAID_API_KEY is not configured in {env_path}")
    return UnraidSettings(api_url=api_url, api_key=api_key)


def current_git_sha() -> str:
    return subprocess.check_output(["git", "rev-parse", "HEAD"], text=True).strip()


def graphql_request(
    client: httpx.Client,
    api_url: str,
    query: str,
    variables: Optional[Mapping[str, Any]] = None,
) -> dict[str, Any]:
    response = client.post(api_url, json={"query": query, "variables": dict(variables or {})})
    response.raise_for_status()
    payload = response.json()
    if not isinstance(payload, Mapping):
        raise RuntimeError("Unraid GraphQL returned a non-object response")
    errors = payload.get("errors")
    if errors:
        messages = []
        if isinstance(errors, list):
            for error in errors:
                if isinstance(error, Mapping):
                    messages.append(str(error.get("message") or "GraphQL error"))
                else:
                    messages.append(str(error))
        raise RuntimeError(f"Unraid GraphQL request failed: {'; '.join(messages) or 'unknown error'}")
    data = payload.get("data")
    if not isinstance(data, Mapping):
        raise RuntimeError("Unraid GraphQL response did not contain data")
    return dict(data)


def inspect_whackamole(client: httpx.Client, api_url: str) -> tuple[str, ContainerSnapshot]:
    data = graphql_request(client, api_url, PREFLIGHT_QUERY)
    try:
        api_version = str(data["info"]["versions"]["core"]["api"])
        containers = data["docker"]["containers"]
    except (KeyError, TypeError) as exc:
        raise RuntimeError("Unraid GraphQL response was missing deployment fields") from exc
    require_supported_api(api_version)
    if not isinstance(containers, list):
        raise RuntimeError("Unraid Docker container response was not a list")
    matches = [item for item in containers if _has_expected_name(item)]
    if len(matches) != 1:
        raise RuntimeError(
            f"Expected exactly one {EXPECTED_CONTAINER_NAME} container, found {len(matches)}"
        )
    snapshot = container_snapshot(matches[0])
    if snapshot.image != EXPECTED_IMAGE:
        raise RuntimeError(
            f"Refusing update: {EXPECTED_CONTAINER_NAME} uses {snapshot.image!r}, expected {EXPECTED_IMAGE!r}"
        )
    return api_version, snapshot


def require_supported_api(version: str) -> None:
    match = re.match(r"^(\d+)\.(\d+)\.(\d+)", version)
    if not match:
        raise RuntimeError(f"Could not parse Unraid API version {version!r}")
    parsed = tuple(int(part) for part in match.groups())
    if parsed < MINIMUM_API_VERSION:
        minimum = ".".join(str(part) for part in MINIMUM_API_VERSION)
        raise RuntimeError(f"Unraid API {version} is too old; updateContainer requires {minimum}+")


def container_snapshot(value: Any) -> ContainerSnapshot:
    if not isinstance(value, Mapping):
        raise RuntimeError("Unraid returned an invalid container record")
    names_value = value.get("names")
    names = tuple(str(name) for name in names_value) if isinstance(names_value, list) else ()
    labels = value.get("labels")
    revision = str(labels.get(REVISION_LABEL) or "") if isinstance(labels, Mapping) else ""
    update_available = value.get("isUpdateAvailable")
    if not isinstance(update_available, bool):
        update_available = None
    return ContainerSnapshot(
        id=str(value.get("id") or ""),
        names=names,
        image=str(value.get("image") or ""),
        image_id=str(value.get("imageId") or ""),
        revision=revision,
        state=str(value.get("state") or ""),
        status=str(value.get("status") or ""),
        update_available=update_available,
    )


def deploy_whackamole(
    *,
    graphql_client: httpx.Client,
    health_client: httpx.Client,
    api_url: str,
    expected_sha: str,
    health_url: str = DEFAULT_HEALTH_URL,
    timeout: float = 300.0,
    poll_interval: float = 10.0,
    check_only: bool = False,
    monotonic: Any = time.monotonic,
    sleep: Any = time.sleep,
) -> DeploymentResult:
    api_version, initial = inspect_whackamole(graphql_client, api_url)
    expected_sha = expected_sha.strip()
    if not expected_sha:
        raise ValueError("Expected Git commit SHA is empty")
    if not initial.healthy:
        raise RuntimeError(
            f"Refusing update: {EXPECTED_CONTAINER_NAME} is not healthy ({initial.state}, {initial.status})"
        )

    if check_only:
        require_health(health_client, health_url)
        return DeploymentResult(action="check", api_version=api_version, container=initial)

    if initial.revision == expected_sha:
        require_health(health_client, health_url)
        return DeploymentResult(action="already-current", api_version=api_version, container=initial)

    graphql_request(graphql_client, api_url, UPDATE_MUTATION, {"id": initial.id})
    deadline = monotonic() + max(0.0, timeout)
    last = initial
    while True:
        _, last = inspect_whackamole(graphql_client, api_url)
        if last.revision == expected_sha and last.healthy:
            require_health(health_client, health_url)
            return DeploymentResult(action="updated", api_version=api_version, container=last)
        if monotonic() >= deadline:
            revision = last.revision[:12] or "unknown"
            raise RuntimeError(
                "Timed out waiting for Whackamole deployment "
                f"(revision={revision}, state={last.state}, status={last.status})"
            )
        sleep(max(0.0, poll_interval))


def require_health(client: httpx.Client, health_url: str) -> None:
    response = client.get(health_url)
    response.raise_for_status()


def format_summary(result: DeploymentResult) -> str:
    container = result.container
    revision = container.revision[:12] or "unknown"
    return " | ".join(
        [
            f"unraid-api {result.api_version}",
            f"whackamole {result.action}",
            f"revision {revision}",
            f"state {container.state}",
            f"health {'healthy' if container.healthy else 'unhealthy'}",
        ]
    )


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Update the live Unraid Whackamole container safely.")
    parser.add_argument("--check-only", action="store_true", help="Validate live deployment state without updating.")
    parser.add_argument("--env-file", type=Path, default=DEFAULT_ENV_PATH, help="Ignored Unraid API env file.")
    parser.add_argument("--timeout", type=float, default=300.0, help="Maximum deployment wait in seconds.")
    parser.add_argument("--poll-interval", type=float, default=10.0, help="Seconds between deployment checks.")
    return parser


def main(argv: Optional[Sequence[str]] = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        settings = unraid_settings(env_path=args.env_file)
        expected_sha = current_git_sha()
        with httpx.Client(
            timeout=30.0,
            headers={"x-api-key": settings.api_key, "Content-Type": "application/json"},
        ) as graphql_client, httpx.Client(timeout=10.0) as health_client:
            result = deploy_whackamole(
                graphql_client=graphql_client,
                health_client=health_client,
                api_url=settings.api_url,
                expected_sha=expected_sha,
                timeout=args.timeout,
                poll_interval=args.poll_interval,
                check_only=args.check_only,
            )
    except httpx.HTTPStatusError as exc:
        print(
            f"Unraid deployment request failed: HTTP {exc.response.status_code} for {exc.request.url}",
            file=sys.stderr,
        )
        return 1
    except Exception as exc:
        print(f"Unraid deployment failed: {exc}", file=sys.stderr)
        return 1
    print(format_summary(result))
    return 0


def _has_expected_name(value: Any) -> bool:
    if not isinstance(value, Mapping):
        return False
    names = value.get("names")
    return isinstance(names, list) and EXPECTED_CONTAINER_NAME in names


if __name__ == "__main__":
    raise SystemExit(main())
