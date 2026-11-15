# Implementation and acceptance checklist

## Implementation sequence

1. Preserve and inspect the existing Python foundations.
2. Finish the application factory, feature routes, secure owner login, SQLite setup and migration.
3. Harden atomic stock operations, exact money, retry keys, historical snapshots and reversals.
4. Connect the responsive Light/Dark/System interface to stored business data.
5. Add guarded demo/setup/backup commands and beginner-friendly documentation.
6. Execute backend tests, concurrent file-backed database tests and browser journeys; fix defects before handoff.

## Required checks

| Area | Acceptance checks |
| --- | --- |
| Authentication | Owner creation; login/logout; protected routes; CSRF; rate limiting; safe redirects; cookie/session settings |
| Catalog | Normalized unique SKU; required fields; zero opening stock/threshold; reject negative/malformed values; archive |
| Inventory | Opening ledger entry; reasoned adjustment; before/after balances; current stock reconciles with movements |
| Sales | Exact and insufficient stock; multiple products; duplicate lines; exact decimal discounts; historical receipt |
| Purchases | Atomic stock increase; immutable line costs; reversal fails if stock would become negative |
| Concurrency | Separate SQLite connections; simultaneous sales, identical keys and reversals; no negative balances |
| Failure handling | Forced failure rolls back; same key/payload replays original result; changed payload rejected |
| Expenses | Create/edit/void; audit trail; voided amounts excluded from active reports |
| Reporting | Inclusive date ranges; consistent void exclusions; current-snapshot inventory labels; CSV escaping/formula safety |
| Browser | Login → product → purchase → sale → expense → dashboard → low stock → segmented filters → quick add → export → theme → refresh |
| Responsive/accessibility | 360/768/1440px; both themes; no page overflow; labels/focus; dialogs; reduced motion; chart summaries |
| Setup | Fresh migration; owner command; guarded fictional seed; safe backup/restore guidance; no automatic seeding |

## Verification record

Recorded on 15 Nov 2026, macOS arm64, Python 3.14.8, SQLite 3.50.4, headless Chromium (Playwright 1.65.2).

| Check | Command | Result |
| --- | --- | --- |
| Unit, service, route, CLI and concurrency tests | `.venv/bin/python -m pytest -q` | 155 passed |
| Browser journey | `.venv/bin/python scripts/browser_qa.py` | All 15 steps passed (see `artifacts/browser-results.json`) |
| Migration drift | `flask --app run db check` | No new upgrade operations |
| Ledger consistency (seeded demo) | `flask --app run reconcile-stock` | 12 products, 75 movements, no inconsistencies |
| Backup and restore | `flask --app run backup …`, restore to a temporary copy | `integrity_check` ok, no foreign-key errors, 30 sales preserved |

Defects found and fixed during verification: CSRF time-limit type mismatch; password changes not revoking other sessions; non-atomic login rate limiting under concurrent attempts; a 101-line form losing input on rejection; oversized requests recursing in the error handler; a screen-reader-only label in tables causing horizontal page overflow on mobile; joined words in the responsive login headline; focus escaping the void dialog on Tab.

Not verified: Windows setup commands, Safari/Firefox, physical printing, screen readers, and any public deployment (out of scope).
