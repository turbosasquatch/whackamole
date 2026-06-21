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

1. Load `.codex/live-api.env`.
2. Fetch the report with `GET /api/reports/{report_id}`.
3. Follow `report.item_id` to `GET /api/items/{item_id}`.
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
