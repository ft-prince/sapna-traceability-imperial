# SAPNA Traceability - Imperial

Read-only Django viewer over the `trace` schema written by Node-RED (Postgres 17).
Pages: `/` (live dashboard, SSE) and `/monitoring/` (search, Excel export, charts). `/admin/` needs a login.

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

`sample_data.py` seeds a LOCAL copy only - never run it against the plant database.
