# Whackamole

Whackamole watches completed torrents from [autobrr/qui](https://github.com/autobrr/qui), maps the torrent path into the path Upload Assistant can see, and runs Upload Assistant in site-check mode. When UA passes a tracker, Whackamole asks Sonarr or Radarr for read-only interactive search results and keeps only the items that look unique or like meaningful upgrades.

This first build is deliberately recommendation-only: it does not upload anything for you.

Whackamole treats Upload Assistant as the authority for tracker eligibility and duplicate checks. Sonarr/Radarr are used only as read-only comparison sources, with usenet results ignored.

## Safety Rails

- First run baselines existing completed torrents by default.
- Completed torrents are deduped by QUI instance ID and torrent hash.
- QUI cross-seeds and uploads are captured as inventory, not queued for UA checks.
- Only one Upload Assistant job runs at a time by default.
- Arr comparisons are serialized and have a hard timeout.
- Upload Assistant job starts are spaced by at least 120 seconds by default.
- Errors back off at 15 minutes, 60 minutes, then 360 minutes.
- The active queue is capped at 250 items by default.
- API keys are encrypted at rest in `/config/secrets.yaml` using `/config/secret.key`.

## Container

The published image is:

```text
ghcr.io/turbosasquatch/whackamole:latest
```

If Unraid is pulling anonymously, make the GHCR package public after the first workflow build.

Default web port:

```text
8383
```

Expected mounts:

```text
/config       rw   Whackamole config, secrets, and sqlite database
/data/torrents ro  Media path as Upload Assistant sees it
/ua-tmp       ro   Optional Upload Assistant temp view
```

## Unraid

Use [unraid/whackamole.xml](unraid/whackamole.xml) as the Docker template. Once the container starts, open the WebUI and save:

- QUI URL, instance ID, and API key
- Upload Assistant URL and bearer token
- Sonarr/Radarr URLs and API keys for upgrade comparison
- Path mappings, usually one per line like:

```text
/media/torrents => /data/torrents
```

Leave API key fields blank after saving to keep the encrypted stored values.

## Baseline Inventory

Whackamole paginates through QUI and stores completed source torrents, cross-seeds, and uploads. Cross-seeds/uploads are used as tracker coverage signals on the dashboard. DP, ULCX, and IHD are highlighted as primary trackers; other detected trackers are shown as secondary coverage.

Use the Baseline view filters to narrow the backlog:

- Media type: all, movie, TV/season pack, or episode.
- Missing tracker coverage: hide rows that already have selected primary tracker coverage.
- Hide any primary coverage: show only rows with no DP/ULCX/IHD coverage.

The `Inventory` tab shows captured cross-seed/upload rows themselves for audit.

Baseline and Inventory views are paginated so large libraries stay responsive. On the Baseline view, `Run check on found set` queues every row matching the current filters; normal UA job spacing and concurrency limits still control how quickly those checks run.

## Maintenance Guard

Whackamole can pause itself around scheduled mover work without editing mover scripts. The default guard starts at 04:30 Europe/London for a 05:00 maintenance window. While active, Whackamole stops QUI polling and UA job starts. It resumes automatically only after QUI has gone down and then becomes healthy again, which matches Unraid mover scripts that stop QUI before file moves and start it afterward.

You can adjust or disable the guard in Settings. The dashboard also has manual pause and resume buttons for one-off maintenance.

## JSON API

Detailed item APIs are read-only and require the Whackamole API bearer token from Settings. `/api/status` stays unauthenticated and only returns lightweight service/configured-state booleans.

```bash
curl -H "Authorization: Bearer <whackamole-token>" \
  "http://unraid-ip:8383/api/items?status=candidate&limit=50"
```

Endpoints:

- `GET /api/items` returns paginated check summaries. Query params: `status`, `limit`, `offset`, `include_details`, `media`, `missing`, `hide_any_primary`.
- `GET /api/items/{id}` returns the full check record, including raw QUI metadata, UA log, tracker buckets, and Arr decisions.
- `GET /api/items/{id}/log` returns the UA log as `text/plain`.

## Local Development

```bash
python -m venv .venv
. .venv/bin/activate
pip install -r requirements-dev.txt
WHACKAMOLE_CONFIG_DIR=./data uvicorn app.main:app --reload --port 8383
```

Run tests:

```bash
pytest
```

Build locally when Docker is available:

```bash
docker build -t whackamole:local .
```
