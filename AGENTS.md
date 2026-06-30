# AGENTS.md

## Live Whackamole API

For local testing that benefits from live data, this repo has an ignored credential file at `.codex/live-api.env`.

Load it with:

```sh
. .codex/live-api.env
```

Use:

- `WHACKAMOLE_API_BASE_URL` as the primary API base URL.
- `WHACKAMOLE_API_FALLBACK_URL` if the primary URL is unreachable.
- `WHACKAMOLE_API_TOKEN` as a bearer token.

Example redacted request shape:

```sh
curl -H "Authorization: Bearer ${WHACKAMOLE_API_TOKEN}" \
  "${WHACKAMOLE_API_BASE_URL}/api/items?limit=1"
```

Do not print token values, include them in logs, commit them, or copy them into tracked files.

The live Whackamole instance already has QUI, Sonarr, Radarr, Upload Assistant, and Profilarr credentials configured server-side. Use Whackamole's API for live test data instead of requiring direct local credentials for those services.

## Whackamole Reporting Issues

When local work refers to an "issue" number, treat it as a Whackamole reporting-system issue unless the user explicitly says GitHub.

Reporting workflow:

1. Prefer `.venv/bin/python tools/live_report.py --report-id {report_id}` with read-only network escalation.
2. Use the JSON path printed by the helper only when deeper inspection is needed.
3. The helper loads `.codex/live-api.env`, fetches `GET /api/reports/{report_id}`, follows `report.item_id` to `GET /api/items/{item_id}`, and prints a compact summary.
4. For decision mismatches, inspect:
   - `checks.media.issues`
   - `checks.media.resolved_mediainfo_issues`
   - `checks.media.media_tags`
   - `checks.media.title_tag_matches`
   - `checks.decision`
   - `checks.flags`

Live API connection process:

- Use `WHACKAMOLE_API_BASE_URL` first.
- Use `WHACKAMOLE_API_FALLBACK_URL` only if the primary URL is unreachable.
- Authenticate with `Authorization: Bearer ${WHACKAMOLE_API_TOKEN}`.
- Request escalated network access up front for read-only live Whackamole API requests, using `curl -fsS`. Sandboxed curls to the live instance are expected to fail and waste time.
- Do not print token values, include them in logs, commit them, or copy them into tracked files.

## Publish And Docker Rebuild

Use this fixed, token-efficient workflow when asked to push to GitHub and update Docker.

Known repo facts:

- Remote: `origin` -> `git@github.com:turbosasquatch/whackamole.git`
- Deployment branch: `main`
- Docker workflow: `.github/workflows/docker-image.yml` / `Docker Image`
- Published image: `ghcr.io/turbosasquatch/whackamole:latest`
- Docker rebuild trigger: push to `main`

Efficient process:

1. Run `git status --short --branch`.
2. Stage only intended files explicitly. Do not use broad `git add -A` in a mixed worktree.
3. Leave local-only state such as `.smoke-ui/` untouched unless the user explicitly names it.
4. Run `git diff --check`, relevant targeted tests, and full `.venv/bin/pytest` when practical.
5. Commit on `main` with a terse message.
6. Push with `git push origin main`.
7. Get the pushed SHA with `git rev-parse HEAD`.
8. Verify the Docker rebuild with `.venv/bin/python tools/docker_workflow_status.py`.

Efficiency rules:

- Do not rediscover the README, workflow, image name, or remote unless the worktree state contradicts the known facts above.
- Prefer the GitHub connector or one compact read-only GitHub Actions API request over `gh`; `gh auth` is not reliably valid in this environment.
- Do not print full GitHub API payloads. Parse to one-line summaries with run id, workflow name, short SHA, status, conclusion, and URL.
- Do not run local Docker unless Docker is known available or the user specifically asks for a local image. GitHub Actions is the canonical rebuild path.
- Final updates should report only the commit SHA, test result, push result, Docker workflow URL/conclusion, and any untouched local-only files.

Repeated task helpers:

- Live report inspection: `.venv/bin/python tools/live_report.py --report-id N`
- Docker rebuild status: `.venv/bin/python tools/docker_workflow_status.py`
- Open report replay: `.venv/bin/python tools/rule_replay.py`
- These helpers are read-only, compact by default, and should be run with network escalation up front when they need live network access.
