# Contributing

Open an issue before making a large change so its scope can be discussed.

## Development setup

Use Python 3.11 or 3.12. Create and activate a virtual environment, install `requirements-dev.txt`, copy `.env.example` to `.env`, and set a local session secret and organizer password. Start the app with `uvicorn app.main:app --reload`.

Before opening a pull request, run `python -m pytest -q` and `node --check web/app.js`.

## Pull requests

- Keep changes focused and explain user-visible behavior.
- Add or update tests for behavior changes.
- Do not commit participant data, generated certificates, credentials, cached fonts, or fonts without redistribution rights.
- Include migration and deployment notes for storage or configuration changes.
- By submitting a contribution, you agree it is provided under AGPL-3.0-only.
