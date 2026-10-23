# CertifyFlow

CertifyFlow is a certificate design and batch generation web app. It runs locally or as a single-organizer hosted service. Templates, participant sheets, generated certificates, and SQLite metadata are stored on the server. The app does not provide accounts for multiple organizations.

## Features

- Design certificate templates from PDF, PNG, or JPG backgrounds.
- Position and style fields, then map them to CSV or Excel participant columns.
- Preview output, generate certificates in batches, retry failed rows, and download a ZIP and report.
- Compress each generated PDF to under 1 MB or report that record as failed.
- Use Google Fonts when online, with a built-in list and offline cache support.
- Manage templates, including deleting one or all templates from the home page.

## Local development

Requirements: Python 3.11 or 3.12. On Windows PowerShell:

```powershell
py -3.11 -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install --upgrade pip
python -m pip install -r requirements-dev.txt
Copy-Item .env.example .env
```

Edit `.env` and replace the example session secret and password. Generate a strong secret with:

```powershell
python -c "import secrets; print(secrets.token_urlsafe(48))"
```

Start the development server:

```powershell
uvicorn app.main:app --reload
```

Open http://127.0.0.1:8000 and sign in with `CERTIBOT_ADMIN_USERNAME` and `CERTIBOT_ADMIN_PASSWORD` from `.env`. API docs are disabled by default. Do not expose the development server publicly.

The default upload limit is 25 MB and expanded XLSX content is capped at 100 MB. A batch accepts at most 1,000 participant rows. Generated PDFs are individually capped below 1 MB.

## Production deployment with Docker Compose

This example runs one app container behind Caddy, which obtains and renews HTTPS certificates. Use a Linux host with Docker Compose, a public DNS name, and inbound ports 80 and 443 available.

1. Copy `.env.example` to `.env` on the host and create a writable backup directory with `mkdir -p backups && sudo chown 10001:10001 backups`.
2. Set `DOMAIN` to the DNS name pointing to the host, `CERTIBOT_ALLOWED_HOSTS` to that same name, and set a unique admin username and password (at least 16 characters).
3. Set `CERTIBOT_SESSION_SECRET` to a fresh random value of at least 32 characters. Keep `.env` private.
4. Start the service with `docker compose up -d --build`.
5. Visit `https://<your-domain>` and sign in.

The app listens only on the internal Compose network; Caddy is the public HTTPS entry point. The app container runs as an unprivileged user and one Uvicorn worker. Its SQLite database, uploads, and outputs live in the persistent `certifyflow_data` volume. The worker is thread-based and process-local; do not scale this deployment to multiple app replicas. This is a single-organizer deployment, not a multi-tenant SaaS setup.

### Backups and restore

Backups contain participant information and certificate files. Protect them like the live data and copy them to a separate encrypted, access-controlled location. Create an archive with:

```sh
docker compose exec app python scripts/backup_data.py
```

Archives are written to `./backups` on the host. Pause generation and wait for active jobs to finish before creating an archive so the database snapshot and output files describe the same state. To restore, stop the app and run `docker compose run --rm --no-deps app python scripts/restore_data.py /backups/<archive-name>.zip`, then start it with `docker compose up -d app`. The restore tool checks archive paths and SQLite integrity before replacing the saved database and files. Test restoration periodically using a separate instance. Do not leave old archives indefinitely; set a retention policy appropriate to your data.

### Configuration

| Variable | Purpose |
| --- | --- |
| `CERTIBOT_ENV` | Use `production` for hosted deployments. |
| `CERTIBOT_SESSION_SECRET` | Random secret used to sign login sessions. |
| `CERTIBOT_ADMIN_USERNAME` / `CERTIBOT_ADMIN_PASSWORD` | One organizer login. |
| `CERTIBOT_COOKIE_SECURE` | Must be `true` in production; cookies are HTTP-only and SameSite strict. |
| `CERTIBOT_ALLOWED_HOSTS` | Comma-separated hostnames accepted by the app. |
| `CERTIBOT_CORS_ORIGINS` | Exact browser origins; leave empty for the bundled same-origin UI. |
| `CERTIBOT_DATA_DIR` | Private persistent data directory. |
| `CERTIBOT_MAX_UPLOAD_MB` | Maximum upload size in MB. |
| `CERTIBOT_MAX_SPREADSHEET_EXPANDED_MB` | Maximum expanded XLSX archive size in MB. |
| `CERTIBOT_ENABLE_API_DOCS` | Set `true` only when API docs are needed. |
| `GOOGLE_FONTS_API_KEY` | Optional Google Fonts catalog API key. |

## Development checks

```powershell
python -m pytest -q
node --check web/app.js
```

CI runs these checks on Python 3.11 and 3.12 on Linux and Windows. CodeQL scans Python code, and Dependabot checks Python and GitHub Actions dependencies weekly. These checks do not replace a security review or deployment monitoring.

## Data, privacy, and limitations

- Treat participant sheets, database files, uploads, generated certificates, and backups as sensitive personal data. Keep the data directory private and define deletion/retention procedures for your deployment.
- The hosted example has one organizer login, no account recovery, no organization or tenant separation, and no multi-user roles. Give credentials only to the intended organizer.
- Keep a single app process. Background job execution is in-process and jobs may need recovery after abrupt termination.
- Back up the persistent data volume and test restoration. Disk space, CPU and memory should be monitored for large batches.
- Verify your legal basis and participant consent, and review privacy, accessibility, licensing, and local data protection requirements before public service.
- Google font files are fetched on first use and cached locally. Review the applicable font license before redistributing font files.

## Open source project

The project is licensed under AGPL-3.0-only; see [LICENSE](LICENSE). PyMuPDF, used for PDF compression, is offered under AGPL or a commercial license; deployments that cannot meet AGPL terms need to assess the commercial option. See [PyMuPDF licensing](https://pymupdf.readthedocs.io/en/latest/about.html). Contributions and vulnerability reporting are described in [CONTRIBUTING.md](CONTRIBUTING.md) and [SECURITY.md](SECURITY.md). Before publishing, create the GitHub repository, enable private vulnerability reporting and dependency/security alerts where available, protect the default branch, and configure DNS and host secrets. No repository has been created or published by this project checkout.

## Project layout

- `app/`: FastAPI endpoints, SQLite storage, PDF rendering.
- `web/`: browser interface.
- `tests/`: API and rendering tests.
- `samples/`: synthetic demonstration files.
- `scripts/`: sample and backup utilities.
- `.github/workflows/`: continuous integration and CodeQL.
