# BizNova operator and technical guide

## Scope and architecture

BizNova is a local-only, single-owner business record application. It tracks catalog entries, stock, sales, purchases and expenses; it does not settle payments, calculate tax, manage credit, reconcile bank accounts or calculate audited profit. All demo records belong to the explicitly fictional **Nova Corner — Fictional Demo**.

Request flow: browser form → protected Flask blueprint → validation/service → SQLite transaction → redirect to the saved record. Jinja templates render the UI; JavaScript may enhance input but never authorizes a financial result. The factory is `create_app(config_name=None, test_config=None)`. It registers extensions, blueprints, SQLite connection pragmas and `register_cli(app)` without seeding data or running migrations automatically.

The service write boundary obtains SQLite `BEGIN IMMEDIATE` before business reads. It refreshes stale ORM state, serializes competing writers and rolls back validation, stock, idempotency and audit failures together. Services own their commits: call them with a clean session, not uncommitted caller modifications. Busy timeouts allow short competing writes to finish, but do not make SQLite a multi-writer database.

## Installation and migration

See [README](../README.md#install-and-run) for complete macOS and Windows commands. The common order, from an activated virtual environment at the project root, is:

```sh
python -m pip install -r requirements.txt
python -m flask --app run db upgrade
python -m flask --app run create-owner
python -m flask --app run run --host 127.0.0.1 --port 5001
```

Before these commands, copy `.env.example` to `.env` only on first installation and set a randomly generated `SECRET_KEY`. Do not use the example placeholder. A normal database uses `DEMO_MODE=false`; leave `DATABASE_URL` blank for the application's `instance/biznova.sqlite` default. Demo mode uses a separate `biznova_demo.sqlite` default. Existing shell variables take precedence over dotenv values.

The short entry point `python run.py` also uses loopback port 5001, avoiding the macOS service commonly listening on port 5000. For regular local operation use:

```sh
waitress-serve --host=127.0.0.1 --port=5001 run:app
```

Use development configuration with debug off and secure cookies disabled for local HTTP. The production configuration forces secure cookies, which browsers do not send over ordinary HTTP. A production-capable WSGI server does not make this application suitable for Internet hosting. Do not bind `0.0.0.0`, open a public tunnel or advertise a public deployment.

Run migrations explicitly on the intended database. Back up before upgrading an existing installation. `db.create_all()` is for disposable test fixtures, not schema upgrades or normal startup. Do not reset or seed a database as an upgrade shortcut.

## First-run and owner account

`python -m flask --app run create-owner` prompts for:

- Username: 3–40 letters/digits/dots/underscores/dashes, normalized to lowercase.
- Display name: 1–120 characters.
- Password entered twice: 12–128 characters, including uppercase, lowercase, digit and symbol.

The password is hashed; there is no seeded account, public signup, second user or fixed demo password. Do not put a real password in shell arguments/history. Creating a second account is refused, including when two owner-creation commands compete for the write lock. This single-owner application does not offer role-based multi-user permissions.

Login is `/auth/login`; logout is a CSRF-protected POST to `/auth/logout`. Invalid sign-in responses do not distinguish an unknown username from a bad password. Failed attempts are limited per client IP in one process (default five attempts in ten minutes). Restarting clears the limiter; multiple processes would have separate counters. It is not a replacement for Internet-facing abuse protection.

Session cookies are HTTP-only and SameSite=Lax. Sessions have an idle timeout. Login clears pre-login session contents; redirects are restricted to safe local paths. Settings can change the password after verifying the current password and confirming the new one. Protect the local OS account, database, `.env` and backup files as well as the web login.

## Daily workflow

### Catalog and inventory

Create a category by entering its name while saving a product. SKU is normalized to uppercase, trimmed and unique. A product records its display name, unit, default purchase and selling prices, opening stock and low-stock threshold.

Opening stock creates an opening ledger movement. Never edit `current_stock` directly. A manual count requires a new whole-unit stock value and a reason; its form carries `expected_stock` to reject a stale count if stock changed after loading. Re-open the form on conflict instead of overwriting another transaction's change.

Archive a discontinued product rather than delete it. Archive preserves all sale/purchase snapshots and history; archived products are excluded from new transactions and active inventory reports. Restore before reusing a product. Low stock means current quantity is less than or equal to its threshold, including zero; out of stock means zero.

### Sale lifecycle

1. Open a new sale. The form receives an idempotency key; preserve it on retries.
2. Choose a business date and add at most 100 distinct products. Combine repeated products into one line; duplicate lines are rejected rather than silently merged.
3. Enter positive whole quantities, optional explicit unit prices and an absolute sale-level discount. Blank line prices use stored product defaults.
4. The server validates active products, product IDs, precision, quantity limits and discount bounds, then computes every line total, subtotal and total using integers.
5. One transaction records the sale, immutable name/SKU/unit/price snapshots, negative stock movements, audit event and idempotency response. Insufficient stock on any line rolls back every line.
6. Retrying the same key and payload returns the original sale without reducing stock again. Reusing a key with different data conflicts. After an uncertain response, retry the original form instead of generating a fresh key.
7. A mistake is corrected by an explicit void with a reason, not deleting a transaction. Void marks status, timestamp and reason, appends compensating stock movements and preserves the original receipt. A second void conflicts and does not reverse inventory twice.

Example: three units at `10.10` are `3030` paise. A `0.01` discount gives `3029` paise (`30.29`). The discount is not 1%, and `10.105` is rejected rather than rounded. Product edits later do not rewrite receipt snapshots.

### Purchases

A purchase snapshots product information and unit cost, adds stock and records its total/audit/idempotency result atomically. It uses the same duplicate-line, date, whole-unit and exact-money rules. A purchase is spending, not an expense category entered twice.

Voiding removes the quantities originally received. If later sales leave insufficient stock, reversal is refused in full: no negative inventory, partial reversal or status-only void is permitted. Investigate/correct the records deliberately; do not invent stock to force a reversal.

### Expenses

Record a positive exact amount, category, business date and optional payment method/description. Edits record before/after values in the audit log. Voiding preserves history but excludes the expense from active reports. A voided expense cannot be edited or voided again.

## Input and financial limits

| Value | Supported range/rule |
| --- | --- |
| Per-line quantity | Integer 1–1,000,000. No fractional quantities. |
| Opening stock / low threshold | Integer 0–1,000,000,000 via product forms. |
| Current stock / explicit adjustment | Integer 0–1,000,000,000. |
| Input monetary amount | ASCII digits, at most nine integral digits and two decimal places; maximum `999999999.99` (99,999,999,999 paise). |
| Sale/purchase aggregate | At most 10^15 paise, checked before persistence. |
| Discount | Absolute input monetary amount, nonnegative and no greater than subtotal. |
| Expense | Strictly positive input monetary amount. |
| Transaction lines | At most 100; no duplicate product IDs. |
| Business date | Required; valid `YYYY-MM-DD`, years 2000–2100. |
| Custom report period | Inclusive start/end; at most 730 days between endpoints. |

Input money does not accept signs, spaces, grouping commas, currency symbols, exponents, NaN or infinity. Display formatting may show currency symbols/grouping; that does not change the input syntax. Floating-point arithmetic is not used for authoritative money. SQL check constraints also enforce integer types, totals and nonnegative stock. The default currency is INR; changing the display currency does not convert historical amounts or provide multi-currency accounting.

## Reports and CSV

Select Today, Last 7/30/90 days, This month or a custom inclusive range. Business dates are date-only values; audit timestamps are UTC and displayed using the configured business timezone. The last 30 days includes today and the previous 29 dates.

- Sales revenue is the sum of completed sales after sale-level discounts.
- Purchase spending includes completed purchases; expenses include active expenses.
- Recorded cash flow subtracts purchase spending and expenses from revenue. It assumes records represent settled transactions and is **not profit**, gross margin, a bank balance or a cash reconciliation.
- Trends include zero-value days. Financial summaries, recent activity and exported financial rows respect the selected period and exclude voided records.
- Top products use line-level **pre-discount** sales totals; sale-level discounts are not allocated to products. Do not expect this ranking's sum to equal net revenue.
- Inventory and low-stock reports show **current stock**, irrespective of the financial period. There is no historical as-of inventory valuation.
- Transaction detail/list views retain voided records for inspection. Active report exports exclude them.
- CSV uses proper quoting and neutralizes formula-leading user text, including dangerous prefixes hidden behind whitespace. An apostrophe may be visible in spreadsheet cells; do not strip it before opening untrusted data. CSV is an export, not a backup.

## Schema and invariants

| Table | Purpose and relationships |
| --- | --- |
| `users` | Single owner created by CLI; normalized username, display name, password hash, last login. Referenced by creators and audit events. |
| `business_settings` | Singleton `id=1`: business display name, currency, timezone, theme and preferences. |
| `categories` | Unique category name; one category to many products. |
| `products` | Unique normalized SKU, category FK, integer prices/current stock/threshold, archive flag. |
| `sales` | Unique reference, business date, subtotal/discount/total, creator FK, completed/voided state. |
| `sale_items` | Sale/product FKs; immutable product name/SKU/unit, quantity, unit price and line total snapshots. |
| `purchases` | Unique reference, supplier/date/total, creator FK and completed/voided state. |
| `purchase_items` | Purchase/product FKs; immutable name/SKU/unit/cost and quantity snapshots. |
| `expenses` | Category/amount/date/payment description, creator FK, active/voided state. |
| `stock_movements` | Product FK, signed delta, balance after, movement type and optional sale/purchase reference. |
| `audit_events` | Event/entity identifiers, summary, optional structured before/after details and owner FK. |
| `idempotency_records` | Unique key, operation scope, request hash and replay response for sale/purchase creates. |
| `alembic_version` | Migration revision tracking; managed by Flask-Migrate/Alembic. |

Ledger types: `opening`, `sale`, `purchase`, `adjustment`, `sale_void`, `purchase_void`. For each product, movement deltas in ID order must form a nonnegative running balance equal to each stored `balance_after`; the final sum must equal `products.current_stock`. `reconcile-stock` checks both the running sequence and final sum in one consistent read snapshot, prints discrepancies and exits nonzero. It does not silently repair the ledger.

The audit and ledger are append-only through application services but not cryptographically tamper-proof. An OS/database administrator can change the file. Database check constraints and foreign keys complement service checks; they do not replace backups or independent accounting review.

## Route map

All business routes require authentication. CSRF is required for browser state-changing requests.

| Routes | Methods / purpose |
| --- | --- |
| `/auth/login` | GET/POST; `username`, `password`, safe `next`. |
| `/auth/logout` | POST only. |
| `/` | GET dashboard with selected period. |
| `/products`, `/products/new` | GET list; GET/POST create form. |
| `/products/<id>`, `/products/<id>/edit` | GET detail; GET/POST edit. |
| `/products/<id>/archive`, `/products/<id>/adjust` | POST archive/restore (`archived=1/0`) and reasoned stock adjustment. |
| `/sales`, `/sales/new`, `/sales/<id>` | GET list/detail; GET/POST new sale. |
| `/sales/<id>/void` | POST reasoned void. |
| `/purchases`, `/purchases/new`, `/purchases/<id>` | GET list/detail; GET/POST new purchase. |
| `/purchases/<id>/void` | POST reasoned reversal. |
| `/expenses`, `/expenses/new`, `/expenses/<id>` | GET list/detail; GET/POST create. |
| `/expenses/<id>/edit`, `/expenses/<id>/void` | GET/POST edit; POST void. |
| `/reports`, `/reports/export/<kind>` | GET HTML or CSV. Kinds: `summary`, `sales`, `purchases`, `expenses`, `top-products`, `inventory`, `low-stock`. |
| `/settings` | GET/POST business preferences or password change. |

Transaction rows use form names `items[0][product_id]`, `items[0][quantity]`, and `items[0][price]` for sales or `items[0][cost]` for purchases. The parent form carries `business_date`, `idempotency_key` and optional customer/supplier/notes; sales carry `discount`. Settings uses `action=business` or `action=password`; password changes use `old_password`, `password`, `confirm_password`.

## CLI reference and demo safety

Every command below is prefixed by `python -m flask --app run`.

| Command | Behavior |
| --- | --- |
| `db upgrade` | Apply migrations explicitly to the selected database. |
| `create-owner` | Prompt/confirm and create the sole owner; never replace an existing owner. |
| `seed-demo` | Require `DEMO_MODE=true` plus exact canonical filename `biznova_demo.sqlite`, refuse populated business tables, then seed fictional service transactions. |
| `reset-demo --confirm RESET-DEMO` | Require exact confirmation and both demo guards; delete business records in FK order inside one transaction; retain owner, migration/schema and file. |
| `backup PATH` | SQLite backup API to a new file; refuse an existing path. Parent directory must exist. |
| `reconcile-stock` | Read-only ledger validation; nonzero exit on inconsistency. |

Demo settings are stored once before seeding transactions, with INR and Asia/Kolkata. The seed has 12 products across Stationery, Pantry, Snacks, Household, Personal Care and Accessories; 30 sales on separate dates; three restocks and five expenses. No username/password is created automatically.

Seeding uses individually committed service operations, not one giant transaction. If interrupted, some earlier records can remain; the next seed refuses a populated DB. Inspect it, explicitly reset the demo database if appropriate, and seed again. Never use reset as a response to a real business error. The guard compares the resolved filename as well as the configured basename, so a demo-named symlink pointing to a differently named real DB is refused. Guards cannot recognize real data deliberately placed in a correctly named demo database.

## Backup and safe restore

1. Keep the database on persistent local disk, with restrictive OS permissions. Do not use network shares, cloud-synchronized folders or disposable storage. SQLite has one writer; long writes block other writers.
2. Create a local backup directory once (`mkdir backups` on macOS; `New-Item -ItemType Directory backups` in PowerShell). Choose a new filename for every backup.
3. Run `python -m flask --app run backup backups/biznova-2026-11-14.sqlite`. This uses SQLite's backup API, so it can capture a consistent committed database while the application is running. Do not copy only the live `.sqlite` file: WAL/journal state may be needed.
4. Verify the backup with Python/SQLite `PRAGMA integrity_check` and a trial restore to a separate path. A backup is not proven useful until restore is exercised. `reconcile-stock` is a logical stock check, not a complete database-integrity check.
5. Keep backups private and on a second secure medium. They contain business history and password hashes; deleting a user from an active DB does not erase old backups.

For restoration, **stop every application/Waitress/Flask process first**. Preserve the current database and any `-wal`, `-shm` or journal sidecars together while stopped; do not delete sidecars from a running database. Restore to a **new** filename, not on top of the current DB or the only backup. The following Python example works on macOS and Windows; replace both paths with absolute paths on your machine and run it in Python:

```python
from contextlib import closing
from pathlib import Path
import sqlite3

source = Path("/absolute/path/to/backup.sqlite").resolve(strict=True)
target = Path("/absolute/path/to/restored-biznova.sqlite")
# On Windows use e.g. Path("C:/BizNova/backups/backup.sqlite").
with target.open("xb"):
    pass  # Exclusive creation: never overwrite a database.
with closing(sqlite3.connect(source.as_uri() + "?mode=ro", uri=True)) as reader:
    assert reader.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
    with closing(sqlite3.connect(str(target))) as writer:
        reader.backup(writer)
        assert writer.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
        assert writer.execute("PRAGMA foreign_key_check").fetchall() == []
```

If verification fails, do not start against the partial target. Investigate and choose a different fresh filename for another attempt. Keep the original backup intact. While still stopped, change `.env` `DATABASE_URL` to the restored file (absolute URI), retaining `DEMO_MODE=false` for real data. Run the appropriate version's `db upgrade`, then `reconcile-stock` before restarting locally. Inspect a known sale and report total. Never run `reset-demo` to restore real data; never restore a real DB under the demo filename.

## Verification and troubleshooting

Run `.venv/bin/python -m pytest` on macOS or `.\.venv\Scripts\python.exe -m pytest` in PowerShell. Tests use `create_app('testing', {'SQLALCHEMY_DATABASE_URI': 'sqlite:///…/test.sqlite', ...})`; each fixture creates its own schema and disposes its connections. Authentication tests normally disable CSRF for focus; dedicated tests enable it and obtain actual tokens from rendered forms.

The suite covers exact money, malformed inputs, unique normalized SKU, whole-unit stock, purchases/sales, overselling, duplicate lines, forced audit failure rollback, archive/snapshots, repeated voids, impossible purchase reversal, expense audit, selected-period reports/void exclusion, CSV injection, route protection, login/CSRF/rate limiting, settings/password and guarded maintenance commands. Concurrency tests use actual file-backed SQLite with independent contexts/thread connections and a barrier before service calls: competing sales, same-key retries, and simultaneous sale/purchase voids.

A passing suite is evidence for those checks on that run, not a guarantee of security, load capacity or browser compatibility. Do not invent pass counts, coverage percentages, deployment results or live-customer claims. Template/browser integration and migrations must be present before claiming a complete run.

Common problems:

- **Missing secret:** generate one and edit `.env`; testing's secret is test-only.
- **Missing table:** verify the URI and run migrations; do not seed to initialize schema.
- **Login loops:** check HTTP versus secure-cookie settings and the chosen port. Do not disable CSRF to work around a form error.
- **Expired token:** reload the form; retain the original transaction idempotency key on uncertain create outcomes.
- **Database busy:** avoid multiple servers and long manual SQLite transactions. Wait for the other writer to finish, then retry the same form/key. Do not delete lock/journal files.
- **Stock conflict:** reload current stock; do not override blindly. Purchase reversal may legitimately be impossible after sales.
- **Reconciliation mismatch:** stop new writes, back up, inspect the affected ledger. There is deliberately no auto-fix command.
- **Port 5001 busy:** stop the other local service or deliberately choose another loopback port; do not broaden the bind address.

## Five-person demonstration and viva plan

This is a suggested generic ownership split, not a claim about who implemented the repository.

| Presenter | Responsibility | Demonstration / question |
| --- | --- | --- |
| Person 1 — Frontend | Design tokens, shared layout, authentication UI and dashboard | Demonstrate responsive navigation, both themes, keyboard focus and chart summaries. |
| Person 2 — Frontend | Product/transaction forms, tables and reports UI | Demonstrate multi-item entry, validation, filters, print and CSV actions. |
| Person 3 — Backend | Authentication, catalog and inventory services | Explain hashing, CSRF, SKU validation, stock adjustments and the ledger. |
| Person 4 — Backend | Sales, purchases, expenses and report queries | Explain atomic posting, idempotency, controlled voids and exact money. |
| Person 5 — Database/support | Schema, migrations, demo seed, tests and backups | Explain SQLite constraints, concurrency evidence, reconciliation and safe restoration. |

### Three-minute demonstration

- **0:00–0:25:** Sign in to the clearly labeled fictional demo. Explain the small-business problem and show the last-30-day dashboard.
- **0:25–0:50:** Open Products, filter low stock and show the out-of-stock item. Explain whole units and the threshold boundary.
- **0:50–1:20:** Record a small purchase of that item, then a sale. Show the changed stock and the saved historical receipt.
- **1:20–1:40:** Add a small expense and show its audit history. Explain that voiding preserves the original record.
- **1:40–2:10:** Open Reports, change the date range and export sales CSV. Explain cash flow versus profit and current-stock snapshots.
- **2:10–2:35:** Switch Light/Dark/System and refresh. Briefly resize to mobile and open the navigation drawer.
- **2:35–3:00:** Show actual test output and `reconcile-stock`. State the single-owner, local-disk, SQLite single-writer limits.

Prepare the app and demo before presenting; do not run a reset during the demonstration.

### Beginner-friendly viva questions

1. **Why Flask and blueprints?** Flask handles HTTP requests; blueprints group routes by business feature so team members can work on separate modules.
2. **Why a service layer?** It keeps validation, stock changes and totals consistent across the website, demo seed and tests.
3. **Why integer paise?** Integers represent amounts such as 10.10 exactly as 1010 paise; binary floating point may not. Unsupported precision is rejected.
4. **What is a database transaction?** A group of writes that commits together or rolls back together; a sale must not reduce stock without saving its receipt and ledger.
5. **How is overselling prevented?** `BEGIN IMMEDIATE` serializes SQLite writers before reading mutable state, conditional stock updates check availability, and database constraints reject negative stock.
6. **What does idempotency mean?** The same create request and key return the original transaction instead of saving it twice. A changed payload with that key is rejected.
7. **Why archive and void instead of delete?** They preserve history and allow explicit, auditable stock reversals exactly once.
8. **Why snapshot item fields?** A later product rename or price edit must not rewrite an old receipt.
9. **Is recorded cash flow profit?** No. It is recorded revenue minus purchases and expenses, assuming settlement; it does not use inventory costing or accounting accruals.
10. **Why migrations rather than create_all?** Migrations track deliberate schema changes; create_all only initializes disposable test fixtures and does not upgrade existing tables.
11. **What do CSRF and password hashing protect?** CSRF tokens help reject forged writes; one-way password hashes avoid storing plaintext account passwords.
12. **Is CSV a backup?** No. A database backup must preserve relationships, history, settings and schema consistently using SQLite's backup API.
