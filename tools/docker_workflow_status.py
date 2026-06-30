from __future__ import annotations

import argparse
import subprocess
import sys
import time
from typing import Any, Dict, Mapping, Optional, Sequence

import httpx


DEFAULT_API_URL = "https://api.github.com"
DEFAULT_REPO = "turbosasquatch/whackamole"
DEFAULT_BRANCH = "main"
DEFAULT_WORKFLOW_NAME = "Docker Image"


def current_git_sha() -> str:
    return subprocess.check_output(["git", "rev-parse", "HEAD"], text=True).strip()


def find_workflow_run(
    *,
    repo: str,
    branch: str,
    sha: str,
    api_url: str = DEFAULT_API_URL,
    timeout: float = 30.0,
    client_factory: Any = httpx.Client,
) -> Dict[str, Any]:
    with client_factory(base_url=api_url.rstrip("/"), timeout=timeout, headers=_headers()) as client:
        response = client.get(f"/repos/{repo}/actions/runs", params={"branch": branch, "event": "push", "per_page": 10})
        response.raise_for_status()
        payload = response.json()
    for run in payload.get("workflow_runs", []) if isinstance(payload, Mapping) else []:
        if not isinstance(run, Mapping):
            continue
        if str(run.get("head_sha") or "") == sha:
            return dict(run)
    return {}


def fetch_workflow_run(
    *,
    repo: str,
    run_id: int,
    api_url: str = DEFAULT_API_URL,
    timeout: float = 30.0,
    client_factory: Any = httpx.Client,
) -> Dict[str, Any]:
    with client_factory(base_url=api_url.rstrip("/"), timeout=timeout, headers=_headers()) as client:
        response = client.get(f"/repos/{repo}/actions/runs/{int(run_id)}")
        response.raise_for_status()
        payload = response.json()
    return dict(payload) if isinstance(payload, Mapping) else {}


def wait_for_workflow_run(
    *,
    repo: str,
    run: Mapping[str, Any],
    api_url: str = DEFAULT_API_URL,
    timeout: float = 30.0,
    poll_interval: float = 10.0,
    max_polls: int = 45,
    client_factory: Any = httpx.Client,
) -> Dict[str, Any]:
    current = dict(run)
    for _ in range(max(1, int(max_polls))):
        if str(current.get("status") or "") == "completed":
            return current
        if poll_interval > 0:
            time.sleep(poll_interval)
        current = fetch_workflow_run(
            repo=repo,
            run_id=int(current.get("id") or 0),
            api_url=api_url,
            timeout=timeout,
            client_factory=client_factory,
        )
    return current


def format_run_summary(run: Mapping[str, Any]) -> str:
    return " | ".join(
        [
            str(run.get("id") or ""),
            str(run.get("name") or ""),
            str(run.get("head_sha") or "")[:7],
            str(run.get("status") or ""),
            str(run.get("conclusion") or ""),
            str(run.get("html_url") or ""),
        ]
    )


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Verify the Docker Image GitHub Actions run for the current commit.")
    parser.add_argument("--repo", default=DEFAULT_REPO, help=f"Repository full name. Default: {DEFAULT_REPO}")
    parser.add_argument("--branch", default=DEFAULT_BRANCH, help=f"Branch to inspect. Default: {DEFAULT_BRANCH}")
    parser.add_argument("--sha", default="", help="Commit SHA to match. Defaults to git rev-parse HEAD.")
    parser.add_argument("--api-url", default=DEFAULT_API_URL, help=f"GitHub API base URL. Default: {DEFAULT_API_URL}")
    parser.add_argument("--timeout", type=float, default=30.0, help="HTTP timeout in seconds.")
    parser.add_argument("--poll-interval", type=float, default=10.0, help="Seconds between run polls.")
    parser.add_argument("--max-polls", type=int, default=45, help="Maximum number of run polls.")
    return parser


def main(argv: Optional[Sequence[str]] = None) -> int:
    args = build_parser().parse_args(argv)
    sha = str(args.sha or current_git_sha())
    try:
        run = find_workflow_run(
            repo=args.repo,
            branch=args.branch,
            sha=sha,
            api_url=args.api_url,
            timeout=args.timeout,
        )
        if not run:
            print(f"No {DEFAULT_WORKFLOW_NAME} push run found for {sha[:7]} on {args.branch}.", file=sys.stderr)
            return 1
        run = wait_for_workflow_run(
            repo=args.repo,
            run=run,
            api_url=args.api_url,
            timeout=args.timeout,
            poll_interval=args.poll_interval,
            max_polls=args.max_polls,
        )
    except httpx.HTTPStatusError as exc:
        print(f"GitHub Actions request failed: HTTP {exc.response.status_code} for {exc.request.url}", file=sys.stderr)
        return 1
    except Exception as exc:
        print(f"GitHub Actions request failed: {exc}", file=sys.stderr)
        return 1
    print(format_run_summary(run))
    return 0 if str(run.get("status") or "") == "completed" and str(run.get("conclusion") or "") == "success" else 1


def _headers() -> Dict[str, str]:
    return {"Accept": "application/vnd.github+json"}


if __name__ == "__main__":
    raise SystemExit(main())
