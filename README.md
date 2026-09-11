# SAPNA Traceability - Imperial

Read-only Django viewer over the `trace` schema written by Node-RED (Postgres 17).
Pages: `/` (live dashboard, SSE), `/monitoring/` (search, Excel export, charts) and
`/recheck/` (login-only; change a failed record's status, with an audit trail).
`/admin/` and `/recheck/` need a login.

## Run on the plant PC (Windows)

```
python -m venv venv
venv\Scripts\pip install -r requirements.txt
set DB_NAME=Imperial_trace
set DB_USER=postgres
set DB_PASSWORD=<pgAdmin password>
set DJANGO_ALLOWED_HOSTS=<this PC's LAN IP>,<this PC's hostname>
venv\Scripts\python manage.py migrate          # Django's own tables only (public schema)
venv\Scripts\python manage.py createsuperuser  # for /admin/
venv\Scripts\waitress-serve --host=0.0.0.0 --port=8000 --threads=16 config.wsgi:application
```

`runserver 0.0.0.0:8000` works for now; waitress is the intended server. Each open
dashboard/modal holds one SSE connection, so keep `--threads` above the number of
operator screens. DEBUG is on until sign-off (`set DJANGO_DEBUG=0` to turn off).

Machine IPs / op codes live in `tracebility/views.py` (`MACHINE_CONFIGS`).

## Self-checks

```
venv\Scripts\python test_timestamp.py
venv\Scripts\python test_machine_data.py
venv\Scripts\python test_monitoring_export.py
```

`sample_data.py` and `demo_feed.py` write to a LOCAL copy only - never run them
against the plant database.

## Recheck page - one-off schema change

The Recheck page stamps who changed a record and when. Those two columns are not
in the original plant schema, so run this ONCE on the plant database before
deploying (machine models are `managed = False`, so Django cannot add them):

```sql
ALTER TABLE trace.ci_postprocessing     ADD COLUMN last_updated_by varchar(150), ADD COLUMN last_updated_at timestamp;
ALTER TABLE trace.auto_postprocessing   ADD COLUMN last_updated_by varchar(150), ADD COLUMN last_updated_at timestamp;
ALTER TABLE trace.helium_postprocessing ADD COLUMN last_updated_by varchar(150), ADD COLUMN last_updated_at timestamp;
```

Node-RED never writes these columns; only the Recheck page does.

Rules the page enforces:

- Only `NG` and `REWORK` records can be changed. An `OK` part, or one the machine
  has not judged yet, is rejected with HTTP 409.
- Choosing "RECHECK" stores the literal `REWORK`, so any Node-RED flow comparing
  against `'REWORK'` keeps working. Operators only ever see the word RECHECK.
- Operators are limited to stations assigned under
  Admin -> Access Control - Operator Stations. No assignment row means
  unrestricted; a row with zero stations locks the operator out entirely.
