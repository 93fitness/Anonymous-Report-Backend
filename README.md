# Anonymous incident reporting backend

Pure Python standard library. No `pip install` needed — just Python 3.9+.

## Run it locally
```
python3 server.py
```
Starts on port 8080 (override with `PORT=xxxx`). Creates `reports.db` (SQLite) on first run.

## Environment variables
| Variable | Default | Purpose |
|---|---|---|
| `PORT` | 8080 | Port to listen on |
| `DB_PATH` | reports.db | SQLite file location |
| `DASH_TOKEN` | demo-token-change-me | Shared token the dashboard sends in `X-Token` — **change this before going live** |
| `ROUTING_PATH` | routing_table.json | Which routing config to load |

## Deploy it for real
This is a single file with no dependencies, so it runs anywhere that can execute `python3 server.py`:
- **Render / Railway / Fly.io**: create a new "Web Service" from this folder, start command `python3 server.py`, no build step needed. Set `DASH_TOKEN` as a secret environment variable.
- **A plain VPS**: run it under `systemd` or `pm2`, and put nginx or Caddy in front of it for HTTPS (the server itself only speaks plain HTTP).
- Once deployed, you'll have a URL like `https://your-service.onrender.com`. Put that into the `API_BASE` constant at the top of both `report.html` and `dashboard.html`, and republish those pages.

## Connecting real report channels
`/api/report` is the one path every channel should ultimately call. To add SMS or WhatsApp:
1. Sign up with a provider — Africa's Talking or Termii for SMS/USSD, Meta's WhatsApp Business Cloud API for WhatsApp.
2. Point their inbound-message webhook at `/webhook/sms` or `/webhook/whatsapp` in this file.
3. In that handler, parse the provider's payload into `{category, description, tags, coords, device_fp}` and call `handle_incoming_report(...)` — the same function the web form already uses. Both stub handlers are marked in `server.py`.

## Known limitations to fix before a real launch
- **Triage is keyword-based**, not ML. It's a reasonable starting point but the keyword lists in `URGENCY_KEYWORDS` need real expansion (ideally with input from your pilot responders) or a swap for a proper NLP classifier.
- **Rate limiting is in-memory** and resets on restart — fine for a demo, not for production scale. Move `RATE_LIMIT` to Redis or the database for a real deployment.
- **Photos/audio aren't stored yet** — the server only records whether a report had media, not the media itself. For production, upload media to object storage (e.g. S3-compatible storage) and store the reference, not a base64 blob, in the database.
- **The dashboard token is a single shared secret.** Fine for a small pilot team; move to per-user accounts before opening the dashboard to more than one agency.
- **Data retention and encryption at rest** aren't configured. Before handling real reports, register as a data controller under Nigeria's Data Protection Act and decide a retention window (e.g. auto-delete resolved, non-evidentiary reports after 90 days).

## Files
- `server.py` — the backend
- `routing_table.json` — category → responder mapping (edit this to add states, LGAs, or new partners without touching code)
