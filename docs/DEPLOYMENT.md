# Deployment

## Local (Docker) — recommended

The app is designed to run entirely in Docker with a **random free host port**.

### Windows PowerShell (automatic free port)
```powershell
./scripts/run_free_port.ps1
```
This picks a random free port in `[10000, 60000]`, confirms it is free, writes
`APP_PORT` into `.env`, and runs `docker compose up --build -d`. It never stops
or kills any existing process. The final URL is printed at the end.

### Manual
```bash
# Set APP_PORT in .env to a free host port, and keep CSRF_TRUSTED_ORIGINS in sync
docker compose up --build -d
docker compose logs -f web        # watch startup
```

There is no fixed port: the host port is whatever you (or the script) set in
`APP_PORT`, and `run_free_port.ps1` picks a new random one each run. Read the
current value from `.env` and open `http://localhost:<APP_PORT>/`.

The container entrypoint automatically:
1. applies migrations,
2. seeds demo data (idempotent),
3. collects static files,
4. starts gunicorn on container port 8000 (mapped to `${APP_PORT}`).

### Persistence
SQLite lives on the named volume `app_db` mounted at `/data`, so data survives
`docker compose down` / rebuilds. Remove it with `docker compose down -v`.

## Retry runner (scheduled)

Process due webhook + CRM retries on a schedule (host cron, Task Scheduler, or a
sidecar):
```bash
docker compose exec web python manage.py process_webhook_retries
```

## Going to production

1. `DEBUG=False`, strong `SECRET_KEY` (unset or empty falls back to an
   obviously-insecure development key).
2. `ALLOWED_HOSTS` + `CSRF_TRUSTED_ORIGINS` set to your domain.
3. `ADMIN_PASSWORD` set to your own strong password — there is no default, and no
   admin account exists until you set one.
4. Processor secrets. `STRIPE_WEBHOOK_SECRET` is what enforces Stripe signature
   verification, and `PAYSTACK_SECRET_KEY` what enforces Paystack's;
   `STRIPE_SECRET_KEY` / `PAYSTACK_SECRET_KEY` additionally drive the derived
   `DEMO_MODE` banner. A present secret is always enforced. See
   [../ENVIRONMENT.md](../ENVIRONMENT.md).
5. Terminate TLS at a reverse proxy (nginx/Traefik/ELB) in front of the
   container; set `SECURE_SSL_REDIRECT=True` and an HSTS value.
6. For higher write volume, migrate from SQLite to PostgreSQL (swap the
   `DATABASES` engine — the ORM code is portable) and run the retry runner as a
   scheduled job.
7. Scale gunicorn workers via the `CMD` in the Dockerfile / compose override.

## Common commands

```bash
docker compose ps                                   # status
docker compose logs -f web                          # logs
docker compose exec web python manage.py seed_demo_data --fresh
docker compose exec web python manage.py create_admin      # needs ADMIN_PASSWORD in .env
docker compose down                                 # stop (keep data)
docker compose down -v                              # stop + wipe data
```
