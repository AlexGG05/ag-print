# AG-Print v0.1 · Bambu P2S + AMS

Self-hosted 3D printing management with **FastAPI + React + PostgreSQL**. Deploy through **Arcane** using the pre-built GitHub Container Registry image. One-person mode by default; no public registration. Docker image builds on each `main` push via `.github/workflows/publish.yml`.

## Features

- Dashboard for printer count, filament inventory, orders and turnover
- Bambu Cloud MQTT monitoring (community protocol, **not official**): print state/progress, remaining time, nozzle/bed/chamber temperature, layers and AMS slots/colors/types as they are reported
- Experimental cloud controls: pause/resume/stop, speed presets, nozzle/bed setpoints via G-code (firmware may reject), launch **an already sliced `.gcode.3mf` file on the printer SD card**
- Inventory, pricing/cost calculator, orders, local admin login, password hashes, PostgreSQL persistence
- No AMS required for inventory but automatically parses slots from your actual AMS cloud reports

**Important:** There is currently **no STL/3MF model editing, no slicing, no 3MF upload, and no Bambu Studio replacement**. MQTT alone does not transfer a print file. Upload/slicing and Cloud print job API integration require further work. Print command publish success is **not confirmation** that the P2S accepted or executed it. Some P2S firmware restricts features. This is a community reverse-engineered integration, and could stop working after a firmware/cloud update.

## Arcane: single copy/paste Compose

1. Wait until GitHub Actions → **Build and publish AG-Print** finishes successfully. In GitHub Packages, set the image's visibility to **public**, or configure GHCR credentials in Arcane; public GitHub repo does not automatically guarantee a public container package.
2. Open Arcane → Stacks → new stack. Paste the YAML below, **replace every placeholder** with your own values; do not commit secrets to GitHub.
3. Deploy; open `http://YOUR-DOCKER-HOST:8086`, sign in with your admin email/password.

```yaml
services:
  db:
    image: postgres:16-alpine
    restart: unless-stopped
    environment:
      POSTGRES_DB: agprint
      POSTGRES_USER: agprint
      POSTGRES_PASSWORD: "REPLACE_DB_PASSWORD"
    volumes:
      - agprint_db:/var/lib/postgresql/data
    healthcheck:
      test: ["CMD-SHELL", "pg_isready -U agprint -d agprint"]
      interval: 5s
      retries: 15
  ag-print:
    image: ghcr.io/alexgg05/ag-print:latest
    restart: unless-stopped
    ports:
      - "8086:8000"
    environment:
      DATABASE_URL: "postgresql+psycopg://agprint:REPLACE_DB_PASSWORD@db:5432/agprint"
      JWT_SECRET: "REPLACE_WITH_64_RANDOM_HEX_CHARACTERS"
      ADMIN_EMAIL: "admin@example.com"
      ADMIN_PASSWORD: "REPLACE_ADMIN_PASSWORD"
      ENABLE_MULTIUSER: "false"
      BAMBU_USER_ID: "REPLACE_BAMBU_USER_ID"
      BAMBU_ACCESS_TOKEN: "REPLACE_BAMBU_ACCESS_TOKEN"
      BAMBU_SERIAL: "REPLACE_BAMBU_SERIAL"
      BAMBU_PRINTER_NAME: "Mein Bambu P2S"
      BAMBU_MQTT_HOST: "us.mqtt.bambulab.com"
    depends_on:
      db:
        condition: service_healthy
volumes:
  agprint_db:
```

The database password above **must be identical in both locations**. URL-encode special characters in the `DATABASE_URL` password or use a random alphanumeric password. For stronger secret handling, use Arcane's environment/secret mechanism instead of saving tokens directly in stack definitions. Keep your Arcane instance private.

Generate a JWT secret on a Linux shell: `openssl rand -hex 32`.

## Cloud credentials (no LAN-only mode needed)

Your Bambu account remains cloud-connected; Bambu Handy/Studio continue to work. Find the **device serial** on your printer (not the friendly name). Cloud broker uses TLS on `us.mqtt.bambulab.com:8883` for Europe/US accounts. `BAMBU_USER_ID` is your Bambu user ID, and `BAMBU_ACCESS_TOKEN` is an account access token (JWT), **not your password**. One way to inspect your own token locally in Firefox: log into `bambulab.com` → F12 → Storage → Cookies → bambulab.com → `token`; copy it only to your private Arcane secrets. Do not send it through chat or GitHub. Token expiry requires refreshing it.

Without all three settings, AG-Print works normally without live printer integration and explicitly shows a non-configured state. There is no demo telemetry passed off as real.

## Safety & limitations

- Only publish through private LAN or behind **Cloudflare Access**; do not expose the printer-control API publicly without an additional gate.
- Printer commands are experimental, require your authenticated admin session, and are sent only if MQTT reports connected. Temperature inputs are limited to 0–300 °C for nozzle and 0–110 °C for bed; whether they are applied depends on firmware. Avoid changing setpoints while printing unless you know what you are doing.
- SD print start needs a **pre-existing sliced** `*.gcode.3mf` file, exact filename and plate number. This does not upload or slice models; certain firmware/cloud setups may reject the command. Confirm physical state before repeated commands.
- AMS inventory is read automatically when the printer supplies AMS tray telemetry. Manual inventory is not silently overwritten; some non-RFID spools lack detailed metadata.
- Cloud MQTT status is received as partial deltas; last-seen is shown to avoid presenting stale state as live.
- Enable multiuser later only after auditing security/isolation. `ENABLE_MULTIUSER=false` disables adding new users.

## Development / local compose

Copy `.env.example` to `.env`, set secrets and run `docker compose up -d` after the image is published. For local image rebuild: `docker build -t ag-print:dev .` and update image in compose.

### Backups

`docker compose exec -T db pg_dump -U agprint agprint > agprint-backup.sql`

### Diagnostics

- `GET /api/health` is public to check readiness.
- `GET /api/bambu/status` requires a signed-in account and reports connection state, last update, and current telemetry.
- `docker compose logs -f ag-print` shows backend startup and broker connection errors.
- Image publish: GitHub → Actions → **Build and publish AG-Print**.

## Reference

Community protocol: https://github.com/Doridian/OpenBambuAPI/blob/main/mqtt.md and https://github.com/mqtt-home/mqtt-bambu . Bambu does not guarantee backwards compatibility.

## Druckstudio – experimentell

Modellbibliothek für STL/3MF mit Upload, Download und Löschen (max. 50 MB). Serverseitiges PrusaSlicer-CLI-Slicing ist nur mit einem **selbst bereitgestellten, validierten** Profil unter `/app/profiles/printer.ini` möglich. Ohne Profil wird Slicing verweigert. Generischer G-Code ist **nicht** automatisch P2S-kompatibel; nicht ungeprüft drucken. Kein direkter Cloud-Datei-Upload, kein 3D-Editor und noch kein Bambu-Studio-Ersatz.

Arcane Compose: Dem `ag-print` Service unter `volumes` `- agprint_models:/app/data` und `- /opt/ag-print/profiles:/app/profiles:ro` hinzufügen. Unter den obersten `volumes` zusätzlich `agprint_models:` eintragen. Im Hostordner `/opt/ag-print/profiles` ein geprüftes `printer.ini` ablegen. API: `/api/studio/models`, `/api/studio/slicer`, `/api/studio/slice` (Login erforderlich).
