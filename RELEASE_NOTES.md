## CertifyFlow v0.1.0

First public release.

### Breaking changes

- Configuration variables use the `CERTIFYFLOW_` prefix. Rename any earlier `CERTIBOT_` settings before upgrading.

### Features

- Design certificate templates from PDF, PNG, or JPG backgrounds.
- Position and style text fields, then map them to CSV or Excel columns.
- Preview certificates, batch-generate up to 1,000 rows, retry failed rows, and download a ZIP and report.
- Compress each PDF to under 1 MB, or report that record as failed.
- Google Fonts support with an offline cache.
- Docker Compose deployment with automatic HTTPS through Caddy.
- Backup and restore scripts.

### Security and limits

- Single organizer login, HTTP-only strict cookies, upload limits, and XLSX expansion caps.
- Single-process deployment by design; this release is not multi-tenant.

### Known limitations

- Background jobs run in-process and may need recovery after abrupt shutdown.
- There is no account recovery or multi-user role system.

Licensed under AGPL-3.0-only.
