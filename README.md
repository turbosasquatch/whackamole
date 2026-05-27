# Whackamole

Whackamole watches completed torrents from [autobrr/qui](https://github.com/autobrr/qui), maps the torrent path into the path Upload Assistant can see, and runs Upload Assistant in site-check mode. The dashboard keeps the items that look worth uploading separate from blocked, errored, baseline, and ignored items.

This first build is deliberately recommendation-only: it does not upload anything for you.

## Safety Rails

- First run baselines existing completed torrents by default.
- Completed torrents are deduped by QUI instance ID and torrent hash.
- Categories containing `cross` and tags containing `cross-seed` are excluded by default.
- Only one Upload Assistant job runs at a time by default.
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
- Path mappings, usually one per line like:

```text
/media/torrents => /data/torrents
```

Leave API key fields blank after saving to keep the encrypted stored values.

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
