# BizNova

A local, single-owner small-business ledger: products, whole-unit inventory, sales, purchases, expenses, period reports and CSV exports. Flask, SQLAlchemy and SQLite provide the server; the UI is server-rendered.

**Local use only.** Bind to `127.0.0.1`; do not expose this application to the Internet, a public tunnel or an untrusted LAN. It is not a multi-tenant accounting system, tax engine, payment processor or audited financial product. Recorded cash flow is **sales revenue − purchase spending − expenses**, not profit.

## Install and run

Use Python 3.11 or newer (verified on 3.14.8) and a persistent local disk. Run these commands from the repository root. Do not overwrite an existing `.env` when upgrading.

### macOS (Terminal)

```sh
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -r requirements.txt
cp .env.example .env
python -c "import secrets; print(secrets.token_hex(32))"
```

Paste the generated value into `.env` as `SECRET_KEY`. Keep it private; do not commit it. Leave `DEMO_MODE=false`, `DATABASE_URL=` and `SESSION_COOKIE_SECURE=false` for a normal local HTTP installation. Then:

```sh
python -m flask --app run db upgrade
python -m flask --app run create-owner
python -m flask --app run run --host 127.0.0.1 --port 5001
```

Open <http://127.0.0.1:5001>. The owner command prompts for a username, display name and a confirmed password of 12–128 characters containing uppercase, lowercase, a digit and a symbol. There are **no default credentials** and only one owner account is allowed.

### Windows (PowerShell)

```powershell
py -3 -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -r requirements.txt
Copy-Item .env.example .env
python -c "import secrets; print(secrets.token_hex(32))"
```

Edit `.env` with your generated secret, using the same local settings above. If activation is restricted, do not weaken system policy: replace `python` in the commands with `.\.venv\Scripts\python.exe`.

```powershell
python -m flask --app run db upgrade
python -m flask --app run create-owner
python -m flask --app run run --host 127.0.0.1 --port 5001
```

`python run.py` starts the same local application at `127.0.0.1:5001`. Port 5001 avoids macOS AirPlay's common use of port 5000. Never add `--debug` for normal use.

### Local production WSGI server

After installation and migrations, use Waitress instead of Flask's development server for regular local operation:

```sh
waitress-serve --host=127.0.0.1 --port=5001 run:app
```

On Windows without activation, use `.\.venv\Scripts\waitress-serve.exe` with the same arguments. This is a production-capable **server**, not permission for public deployment. Keep `BIZNOVA_ENV=development` with `SESSION_COOKIE_SECURE=false` on plain loopback HTTP; debug remains off. The `production` configuration requires secure cookies and is unsuitable for plain HTTP login. A supported HTTPS deployment/security design is outside this project's scope.

### Temporary public demo link (Cloudflare quick tunnel)

For a live demo, serve with secure cookies and trust Cloudflare's client-IP header only from the local tunnel, then open a quick tunnel in a second terminal:

```sh
BIZNOVA_ENV=production SESSION_COOKIE_SECURE=true CLIENT_IP_HEADER=CF-Connecting-IP waitress-serve --host=127.0.0.1 --port=5001 --threads=8 run:app
cloudflared tunnel --no-autoupdate --url http://127.0.0.1:5001
```

The printed `https://….trycloudflare.com` address works only while this computer is awake and both commands run; it changes on every restart. Anyone with the link reaches the sign-in page, so use fictional demo data only and stop the tunnel (Ctrl+C) after the demo.

## Configuration

| `.env` key | Meaning |
| --- | --- |
| `SECRET_KEY` | Locally generated random session/CSRF secret. Missing/placeholder values are rejected outside the supplied testing configuration. |
| `BIZNOVA_ENV` | `development`, `testing` or `production`. Use development for local HTTP; tests select testing explicitly. |
| `DEMO_MODE` | `true` enables the guarded demo commands; otherwise `false`. |
| `DATABASE_URL` | Blank selects the local `instance/biznova.sqlite`, or `instance/biznova_demo.sqlite` in demo mode. Prefer an absolute SQLite URI for custom locations. |
| `SESSION_COOKIE_SECURE` | `false` only for loopback HTTP; secure cookies need HTTPS. Production configuration forces secure cookies. |

For an absolute database path, macOS uses `sqlite:////absolute/path/biznova.sqlite`; Windows uses `sqlite:///C:/absolute/path/biznova.sqlite`. Restart after changing configuration. Environment variables already set in your shell can override `.env`; check the actual target before maintenance.

## Fictional demo

Use a **separate** database. In `.env` set `DEMO_MODE=true` and leave `DATABASE_URL=` (or explicitly select an absolute path whose canonical filename is exactly `biznova_demo.sqlite`). Stop the server before switching databases, then:

```sh
python -m flask --app run db upgrade
python -m flask --app run create-owner
python -m flask --app run seed-demo
python -m flask --app run reconcile-stock
```

The demo is **Nova Corner — Fictional Demo**: 12 varied products, 30 sales spread across the last 30 business dates, three purchases, five expenses, low stock and an out-of-stock item. All names and transactions are fictional. Seeding creates no account, reads no fixed password and never runs at startup. Run `create-owner` separately only if the selected database has no owner.

`seed-demo` refuses a populated business database. To deliberately erase **only the selected demo's business data**, preserving the owner and schema:

```sh
python -m flask --app run reset-demo --confirm RESET-DEMO
python -m flask --app run seed-demo
```

Both demo mode and the exact canonical demo filename are required. Reset uses transactional deletes in foreign-key order, not schema drops or file deletion. The filename guard is not a data classifier: never rename a real business database to the demo filename.

## Back up and test

Create the destination directory first. The backup file itself must not exist:

```sh
mkdir backups
python -m flask --app run backup backups/biznova-first-backup.sqlite
python -m flask --app run reconcile-stock
```

Backup uses SQLite's online backup API, including committed data rather than an unsafe live file copy. Backups contain sensitive business data and password hashes. Keep them private and verify them; see [safe restore steps](docs/guide.md#backup-and-safe-restore).

Run the test suite:

```sh
.venv/bin/python -m pytest
```

Windows:

```powershell
.\.venv\Scripts\python.exe -m pytest
```

Tests create disposable file-backed databases via `create_app('testing', test_config)` and `db.create_all()` in fixtures. They do not run against your configured business database. The concurrency tests use real separate SQLite thread connections, not in-memory substitutes. The latest full local pytest run (macOS, Python 3.14.8, SQLite 3.50.4) completed with **153 passed**. This is a recorded run, not a claim about public-deployment readiness or future results.

### Browser acceptance check (optional)

```sh
.venv/bin/python -m pip install -r requirements-browser.txt
.venv/bin/python -m playwright install chromium
.venv/bin/python scripts/browser_qa.py
```

The script starts its own temporary server and disposable database, drives headless Chromium through the main journey, and writes screenshots plus `browser-results.json` to `artifacts/` only when every step passes. The latest run passed all 12 steps: login, product, purchase, sale with discount, expense, dashboard totals, low stock, CSV export, both themes at 360/768/1440px without page overflow, mobile drawer and void dialogs (focus trap and Escape), server-unavailable and oversell failures that keep input and the retry key, and no JavaScript errors. Safari, Firefox, Windows and physical printing were not tested.

## Operating limits

- Quantities are whole units, not fractional weight/volume. Packaged items can use `pack`, `bottle` or other unit labels.
- Money is exact integer paise. Enter plain decimal numbers with at most two decimal places; invalid precision is rejected, never rounded.
- A discount is an absolute monetary amount, not a percentage. Top-product totals are before sale-level discounts.
- Inventory reports are current snapshots even when financial reports select a past period. Voided transactions are excluded from active report totals and exports.
- SQLite has one writer at a time. Keep the database on persistent local disk, never a network share, sync folder or ephemeral container filesystem. This is not designed for high write concurrency or multiple business locations.
- Login rate limiting is in-process, resets on restart and is not shared across workers. Use one local server process; it is not distributed abuse protection.
- Browser previews are not authoritative. The server validates prices, quantities, totals, stock and idempotency keys before committing.

See [the operator and technical guide](docs/guide.md) for schema, routes, lifecycle, recovery, limitations and a five-person demonstration plan.
