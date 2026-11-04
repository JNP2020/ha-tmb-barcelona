# Contributing

Issues and pull requests are welcome.

## Setup

```bash
python -m venv .venv && . .venv/bin/activate
pip install -r requirements_test.txt
```

## Before opening a PR

```bash
ruff check custom_components tests
pytest
```

CI runs the same two commands plus HACS and hassfest validation.

## Guidelines

- Keep parsing/classification logic pure (no network, no Home Assistant
  objects) so it can be unit-tested directly, as in `alerts.py` and the
  helpers in `coordinator.py` / `gtfs.py`.
- Treat every TMB response as untrusted: read fields defensively and drop
  malformed entries rather than raising.
- Bump `version` in `custom_components/tmb/manifest.json` and add a
  `CHANGELOG.md` entry for any user-visible change (the version also
  cache-busts the timetable card).
- Never log or commit `app_id` / `app_key`.
