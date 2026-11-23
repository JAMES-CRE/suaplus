# Sua+

Django/PostgreSQL learning passport, workshop, assessment and scoped reporting application.

## Development

Use Python 3.12, install `requirements-dev.txt`, and copy `.env.example` to untracked `.env`. Configure a development PostgreSQL database. Run `npm ci --ignore-scripts`, `npm run build:css`, `python manage.py migrate`, then `python manage.py runserver` against the selected development DB.

External UITS/Google, Registry, SMTP, SMS and memo credentials are intentionally blank. Console mail/local templates are development fallbacks, not production acceptance.

## Verify

```sh
npm run test:ui
npm run build:css
python manage.py collectstatic --noinput
python manage.py check
python manage.py makemigrations --check --dry-run
python -m pytest -q --cov=apps.accounts.permissions --cov=apps.imports.views --cov=apps.programmes.services.tier_engine --cov=apps.reporting.services.kpis --cov-report=term --cov-report=json
python scripts/check_coverage.py coverage.json
```

Every listed runtime module must independently reach 70%; tests/migrations are excluded. Live acceptance uses an actual HTTP socket, CSRF sessions, password/TOTP login and a disposable pytest PostgreSQL database, never the configured application DB.

## Workflow rules

- Completion awards Level 1 attendance evidence. Higher internal levels require a scoped staff assessment, completed source attendance, configured evidence-rule cap and no self-award.
- Session console links to assessments and import history. Undo retains participants, consent, timestamps and audit; it withdraws source attendance and revokes attendance evidence. Later completion/assessment needs reconciliation first.
- SAS links have a separate purpose, expire after 24 hours and are consumed once. All active/published KNUST questions are validated; self-ratings do not earn verified KPIs or tiers.
- Coverage uses the latest matching headcount snapshot, adds categories and avoids double-counting parent roll-ups. Progression uses chronological first/latest verified observations including zero/negative changes.
- Trainer activity is programme-specific. Cascade uses unique completed trainees and requires second-generation training to follow the initial training.
- Production badge signing requires dedicated asymmetric keys. DEBUG-only local badges are not accepted in production. Independent Open Badges interoperability validation remains required.

See [operator runbook](docs/operations.md) and [staging acceptance](docs/staging-acceptance.md). Local verification is not institutional sign-off or permission to deploy.
