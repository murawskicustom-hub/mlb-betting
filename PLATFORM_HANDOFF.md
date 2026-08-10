# Sports Betting Platform — Build Handoff

**Purpose:** carry the *platform* design forward into a fresh multi-sport (NFL +
college football, extensible) rebuild with **3 pluggable betting bots**. This
documents the plumbing that worked — data ingestion, storage, scheduling, cloud
execution, dashboard, auth, notifications, and the *contract* a bot plugs into.

**Out of scope on purpose:** the betting math. The previous engines (market
de-vig consensus, a Pythagorean model, F5/YRFI/NRFI markets) are being fully
redesigned. This doc defines the **seam** each new bot implements, not the
algorithms themselves.

> **Assumptions made while writing** (correct me and I'll revise):
> 1. A "bot" is a first-class, registered unit so the 3 bots can be swapped/added
>    without touching the platform. This model fits whether your bots are 3
>    algorithms on a shared slate, 3 sports, or 3 strategies.
> 2. The schema is **sport-agnostic** — `sport` is a column/dimension, add a
>    sport by config, not by forking.
> 3. Same stack: Python, SQLite→Postgres (Neon) dual backend, Streamlit
>    dashboard + password gate, GitHub Actions scheduled pulls, Discord notify.

---

## 1. Design principles that made this work (keep these)

1. **One connection abstraction, every caller goes through it.** Scripts and the
   dashboard never open their own DB handle — they call `get_connection()`. That
   single choke point is what made the SQLite→Postgres migration a ~1-file change.
2. **Dev on a local file DB, run in the cloud on managed Postgres.** SQLite for
   fast local iteration; Postgres (Neon) for the always-on hosted truth. A single
   `DB_BACKEND` env var flips between them.
3. **Idempotent data pulls.** Every pull can run twice with no divergence
   (`INSERT OR IGNORE` / `ON CONFLICT`, natural keys, unique indexes). This is
   what let the laptop scheduler and the cloud job run in parallel safely.
4. **Bots are additive, never destructive.** A bot writes *recommendations*; it
   never mutates ingested data. Multiple bots coexist by tagging every row with a
   `bot_key`. Comparing bots is a `GROUP BY bot_key`.
5. **Shadow-first.** Every pick can be recorded as "shadow" (tracked, not
   staked). New/unproven bots run shadow; you promote to bettable by config, not
   code. This is how you trust a bot before risking units on it.
6. **The dashboard is read-mostly and gated.** It renders data and captures a few
   writes (manual bets, settings). Nothing renders before authentication.
7. **Durable logging lives in the DB, not files.** A `pulls` table is the audit
   trail (API budget, counts, success). Cloud runners have no persistent disk, so
   file logs are ephemeral — the DB record is the source of truth.

---

## 2. High-level architecture

```
                 ┌─────────────── INGESTION ───────────────┐
  schedule/odds/ │ pull_* scripts (idempotent, per source) │
  stats/results  └───────────────────┬─────────────────────┘
                                      ▼
                         ┌────────────────────────┐
                         │  DATABASE (abstracted)  │  SQLite (dev) / Postgres (prod)
                         │  get_connection()       │  switched by DB_BACKEND
                         └───────┬─────────────┬───┘
                                 ▼             ▼
                 ┌──────────────────────┐   ┌──────────────────────┐
                 │  BOT REGISTRY        │   │  PLATFORM SERVICES    │
                 │  bot.generate(ctx)   │   │  grader, CLV, units,  │
                 │  -> recommendations  │   │  calibration          │
                 └──────────┬───────────┘   └──────────┬───────────┘
                            ▼                           ▼
                 ┌───────────────────────────────────────────────┐
                 │  recommendations (bot_key, sport, market, ...) │
                 └───────────────┬───────────────┬───────────────┘
                                 ▼               ▼
                       ┌──────────────┐   ┌──────────────────┐
                       │ DASHBOARD    │   │ NOTIFY (Discord) │
                       │ (gated)      │   │ per-pick + digest│
                       └──────────────┘   └──────────────────┘

  Orchestrator runs "slots" (morning/midday/pregame/closing):
    pulls → grader/CLV → each registered bot → notify
  Run in TWO places against the SAME Postgres:
    • laptop: Windows Task Scheduler  • cloud: GitHub Actions cron
```

---

## 3. Tech stack (carry forward)

| Layer | Choice | Why it earned its place |
|---|---|---|
| Language | Python 3.11 | Pin it (see §12 gotcha). |
| Dev DB | SQLite (WAL mode) | Zero-setup local iteration; frozen as rollback during migration. |
| Prod DB | Postgres on Neon | Serverless, generous free tier, standard `psycopg2`. |
| DB access | thin wrapper (no ORM) | Kept queries readable; dialect handled in one place. SQLAlchemy is installed but only the Core engine is worth using if you want it. |
| Dashboard | Streamlit | Fast to build, multipage, phone-friendly, easy Cloud deploy. |
| Hosting | Streamlit Community Cloud | Free, GitHub-connected, secrets UI. |
| Scheduling | GitHub Actions cron (+ optional local Task Scheduler) | Free, no server, logs in the run UI. |
| Notifications | Discord webhooks | Dead simple, no app to build, phone push for free. |
| Secrets | `.env` (local) / GitHub Actions secrets / `st.secrets` (Cloud) | Three surfaces, one precedence helper (see §11). |

**If you reconsider anything:** the two most swappable pieces are the dashboard
(Streamlit → could be a JS frontend if you outgrow it) and notify (Discord →
Telegram/SMS). Everything else is load-bearing and cheap.

---

## 4. The multi-bot model (the important part)

The previous version hardcoded two engines via an `algo` string column
(`'devig'`, `'model_v1'`). That worked but didn't scale to "3 bots I can change
easily." **Generalize it to a registry.**

### 4.1 Bot as a plugin

```python
# bots/base.py
from dataclasses import dataclass

@dataclass
class Pick:
    sport: str
    game_id: str
    market: str            # 'moneyline' | 'spread' | 'total' | <your markets>
    side: str              # 'home'|'away'|'over'|'under'|...
    line: float | None
    fair_prob: float       # bot's estimate
    edge_pct: float        # vs market (or 0 for market-less "paper" picks)
    confidence: str        # tier label, e.g. 'green'|'yellow'|'red' (platform-defined)
    is_shadow: bool        # tracked-only vs bettable
    notes: str = ''

class Bot:
    key: str               # stable id, e.g. 'nfl_edge_v1' — used everywhere
    display_name: str
    sports: tuple[str, ...]  # which sports this bot covers

    def generate(self, ctx: "BotContext") -> list[Pick]:
        """Read from ctx (games, odds, features); return picks. NEVER writes DB."""
        raise NotImplementedError
```

```python
# bots/registry.py
_REGISTRY: dict[str, Bot] = {}
def register(bot: Bot): _REGISTRY[bot.key] = bot
def all_bots() -> list[Bot]: return list(_REGISTRY.values())
def bots_for_sport(sport): return [b for b in _REGISTRY.values() if sport in b.sports]
```

The orchestrator just does:

```python
for bot in registry.all_bots():
    picks = bot.generate(ctx)
    write_recommendations(conn, bot.key, picks)   # platform owns persistence
```

**Adding/replacing a bot = add a file + `register()`.** No orchestrator, schema,
or dashboard change. That is the "easy to change" you asked for.

### 4.2 What the platform owns vs what the bot owns

| Platform owns (don't reinvent per bot) | Bot owns (your redesign) |
|---|---|
| Ingested games, odds, stats/features | The model / edge logic in `generate()` |
| Persistence of picks (`bot_key`-tagged) | Which markets/sides it emits |
| Grading picks against results | Its own feature needs (declare them) |
| CLV (closing-line value) | Its probability estimates |
| Unit accounting & bankroll | Its confidence tiering *inputs* |
| Calibration reporting | — |
| Confidence tiers & shadow/stake **mechanics** | The **thresholds** (config, per bot) |

> Note: confidence tiers, shadow flag, and unit staking are *platform mechanics*
> that proved valuable and are algorithm-agnostic — keep them. But make the
> **thresholds per-bot config** (a `bots/config.py` or a `bot_config` table), not
> constants baked into one engine. The old build learned this the hard way when a
> single green threshold mislabeled longshots across the board.

### 4.3 `BotContext`

Give `generate()` everything it needs, already normalized, so bots stay pure and
testable:

```python
@dataclass
class BotContext:
    conn: object              # read-only use
    sport: str
    as_of_utc: str
    games: list[dict]         # today's slate for this sport
    odds: dict                # game_id -> normalized consensus/best prices
    features: dict            # game_id -> whatever stats you ingested
```

---

## 5. Sport-agnostic data model

Make `sport` a first-class dimension. Sketch (types shown Postgres-side; SQLite
uses the same columns with flexible typing):

```
sports(sport TEXT PK, display_name TEXT, active INTEGER)

games(
  game_id TEXT PK,          -- provider id or synthesized "{sport}:{provider_id}"
  sport TEXT,               -- FK sports.sport
  game_date TEXT, start_utc TEXT,
  home_team TEXT, away_team TEXT, home_team_id TEXT, away_team_id TEXT,
  venue TEXT, status TEXT, home_score INTEGER, away_score INTEGER,
  last_updated_utc TEXT
)

odds_snapshots(
  id IDENTITY PK, game_id TEXT, sport TEXT,
  book TEXT, market TEXT, outcome_type TEXT, line REAL,
  price_american INTEGER, price_decimal REAL,
  snapshot_time_utc TEXT, api_last_update_utc TEXT,
  UNIQUE(game_id, book, market, outcome_type, snapshot_time_utc)  -- dedup!
)

features(                    -- generic per-game feature bag (replaces sport-specific stat tables)
  game_id TEXT, sport TEXT, as_of_date TEXT,
  key TEXT, value REAL, value_text TEXT,
  UNIQUE(game_id, as_of_date, key)
)   -- or keep typed stat tables per sport if you prefer; features-bag scales to many sports cheaply

recommendations(
  id IDENTITY PK,
  bot_key TEXT,             -- <<< replaces the old single 'algo' column
  sport TEXT, game_id TEXT,
  generated_at_utc TEXT,
  market TEXT, side TEXT, line REAL,
  target_price_american INTEGER, fair_price_american INTEGER,
  edge_percent REAL, confidence TEXT,
  is_shadow INTEGER, recommended_stake_units REAL,
  num_books_in_consensus INTEGER,
  closing_price_american INTEGER, clv_percent REAL,
  result TEXT, unit_profit REAL, graded_at_utc TEXT,
  model_probability REAL, notes TEXT,
  config_version TEXT       -- bump when a bot's thresholds change, to segment before/after
)

bets(                        -- your manually-placed bets, optionally linked to a rec
  id IDENTITY PK, sport TEXT, game_id TEXT, placed_at_utc TEXT,
  book TEXT, market TEXT, side TEXT, line REAL,
  price_american INTEGER, stake_units REAL, recommendation_id INTEGER,
  closing_price_american INTEGER, clv_percent REAL,
  result TEXT, unit_profit REAL, graded_at_utc TEXT
)

results(                     -- generic box/линescore per game (was 'linescores')
  game_id TEXT PK, sport TEXT, detail_json TEXT,   -- sport-specific detail as JSON
  home_final INTEGER, away_final INTEGER, status TEXT, last_updated_utc TEXT
)

pulls(id IDENTITY PK, pull_time_utc TEXT, sport TEXT, source TEXT,
      requests_remaining INTEGER, requests_used INTEGER, success INTEGER, error TEXT)

settings(key TEXT PK, value TEXT, updated_at_utc TEXT)
```

Key moves vs the old MLB schema:
- **`algo` → `bot_key`**, everywhere. Index it.
- **`sport` on every row.** Index `(sport, game_date)`.
- **`features` bag** instead of sport-specific stat tables (pitcher_stats,
  team_offense_stats). One table serves every sport; a bot reads the keys it
  needs. (If you prefer strong typing, keep per-sport tables — but the bag is why
  adding a sport becomes config, not a migration.)
- **`results` with `detail_json`** replaces the MLB-specific `linescores`
  (inning arrays). Football box detail (quarters, drives) goes in the JSON.
- **`config_version`** generalizes the old `classification_version` — lets you
  compare "before vs after" whenever you retune a bot.
- Units, not dollars, as the native currency (a `bankroll` setting converts for
  display). Keeps math bankroll-independent.

---

## 6. Database abstraction (the choke point)

`get_connection()` returns either a real `sqlite3` connection or a thin wrapper
over `psycopg2` that makes Postgres speak SQLite's dialect so **call sites never
change**. Reimplement this first — everything depends on it.

The wrapper must translate, at `execute()` time:
- `?` and `:name` placeholders → `%s` / `%(name)s`
- `last_insert_rowid()` → `lastval()`
- `INSERT OR IGNORE` → `INSERT ... ON CONFLICT DO NOTHING`
- `INSERT OR REPLACE` → `INSERT ... ON CONFLICT (keys) DO UPDATE` (via a helper)
- `datetime('now')` / `date('now', ...)` → Postgres equivalents
- register a Decimal→float adapter (Postgres aggregates return `Decimal`; SQLite
  returns `float` — mismatched arithmetic will crash otherwise)
- return rows that support **both** `row[0]` and `row['col']` and `dict(row)`
  (mimic `sqlite3.Row`)

`init_db()` creates the SQLite schema for dev; on Postgres it's a no-op (schema is
owned by a `schema_postgres.sql` you apply once). Keep a `DB_BACKEND` env var,
default `sqlite` locally, `postgres` in cloud/dashboard.

---

## 7. Ingestion & scheduling

- **One pull script per source** (`pull_schedule`, `pull_odds`, `pull_results`,
  `pull_features/stats`). Each is idempotent and writes a `pulls` audit row.
- **Orchestrator** (`run_slot.py <slot>`) runs a slot's pulls, then grader/CLV,
  then every registered bot, then notify. Use `sys.executable` for subprocesses
  so the same code runs on your laptop and on a cloud runner.
- **Two executors, same Postgres:**
  - GitHub Actions cron (`.github/workflows/pulls.yml`) — the primary, always-on.
  - (Optional) local Task Scheduler for redundancy during bring-up.
- Cron is **UTC**; comment the local-time mapping and revisit at DST changes.
- Respect API budgets — read `requests_remaining` from the odds API and log it;
  gate expensive pulls behind the slots that need them.

---

## 8. Dashboard + auth gate

- Multipage Streamlit app; `app.py` is the entry, `pages/` for the rest.
- **Every page** calls `require_login()` **after** `set_page_config` +
  `inject_custom_css` and **before** any DB/query/content. Streamlit executes each
  page file top-to-bottom on direct load, so a per-page gate is the only thing
  that stops deep-link bypass. Gate all pages independently and test each URL.
- `require_login()`: read password from `st.secrets["APP_PASSWORD"]` only (no
  default), constant-time compare (`hmac.compare_digest`), store only a boolean
  `authenticated` flag in `session_state` (never the password), `st.stop()` until
  authed.
- The gate is also the natural place to bootstrap config: copy `DATABASE_URL`
  from `st.secrets` into `os.environ` and default `DB_BACKEND=postgres`, so the
  same `get_connection()` works on Cloud and locally.
- Add a **bot filter** and a **sport filter** to the dashboard from day one —
  with 3 bots × N sports you'll want to slice by both. All queries already carry
  `bot_key` and `sport`, so it's a `WHERE`/`GROUP BY`.

---

## 9. Notifications

- `notify.py`: `send_pick_alert(pick, game)` (per-pick, fire when a bettable pick
  is written) and `send_digest(picks, slot)` (one grouped message per slot).
- Fire notifications **from the pulls/orchestrator**, not the dashboard (the
  dashboard shouldn't need webhook secrets).
- Wrap every send in try/except — a webhook outage must never crash a pull.
- With 3 bots, either one channel per bot or one channel with the `bot_key` in
  the message. Per-channel is cleaner for muting a noisy bot.

---

## 10. Secrets & config

Three surfaces, one precedence order: **`st.secrets` → `os.environ`/`.env`**.
- Local: `.env` (gitignored, never committed). Confirm with `git check-ignore`.
- Cloud dashboard: Streamlit **Secrets** UI (`secrets.toml` format).
- Cloud pulls: **GitHub Actions secrets**.
- Secrets needed: `APP_PASSWORD`, `DATABASE_URL`, odds API key, Discord
  webhook(s), `DB_BACKEND`.
- **Never** commit secrets; keep `.env`, `*.db`, `secrets.toml` in `.gitignore`.
  Scan tracked files before making a repo public.

---

## 11. Migration & environment playbook (proven sequence)

1. Build and iterate on **SQLite** locally.
2. Stand up **Neon**; apply `schema_postgres.sql`; migrate data id-for-id and
   reset identity sequences to `MAX(id)`.
3. **Verify exhaustively before trusting**: row counts per table, content
   spot-checks, FK integrity, aggregate sums, sequence > max id.
4. Introduce the `DB_BACKEND` switch; test the whole app on Postgres **and**
   confirm SQLite still works.
5. Point the cloud (Actions + dashboard) at Postgres; keep SQLite frozen as a
   rollback net until the cloud has run clean for a few days.
6. Only then retire the local executor.

---

## 12. Hard-won lessons / gotchas (read before you rebuild)

- **Pin Python.** Streamlit Cloud will happily build on the newest Python (we got
  3.14) and crash at startup. Add a `.python-version` (`3.11`) — the uv-based
  Cloud builder respects it — and match it in Actions.
- **SQLite `WAL` mode** + `busy_timeout` so a long-lived reader (the dashboard)
  never blocks the writer (the scheduler). A stray dashboard process holding the
  DB open caused an all-night stall once.
- **Task Scheduler "Interactive only" logon + a sleeping laptop = missed jobs.**
  If you keep a local executor, it must run whether-logged-on-or-not and the
  machine must stay awake (or the cloud must be primary). This is why Actions is
  the primary executor now.
- **Secrets and BOMs.** Piping a secret value through PowerShell (`x | gh secret
  set`) prepended a UTF-8 BOM that corrupted `DATABASE_URL` and the API key in
  CI. Set secrets from a file (`gh secret set --env-file`) or the UI, and read
  `.env` with `utf-8-sig`.
- **Postgres rejects `SELECT DISTINCT ... ORDER BY <col not in select>`** (SQLite
  allows it). Use `GROUP BY` instead.
- **Postgres aggregates return `Decimal`, SQLite returns `float`.** Register a
  Decimal→float cast in the connection wrapper or arithmetic will crash.
- **Dedup at write time, not read time.** A same-run race wrote duplicate picks
  once; a `UNIQUE` index + `ON CONFLICT` is the durable fix.
- **Confidence on one axis lies.** Tiering picks on edge alone mislabeled
  high-edge longshots as high-confidence; they lost money. Whatever your bots do,
  make the platform's tiering support a **dual-axis** gate (e.g. edge AND
  probability floor) and keep thresholds as per-bot config with a `config_version`
  so you can measure before/after.
- **Never render before auth.** Gate every page independently; test deep links.

---

## 13. Suggested repo layout for the rebuild

```
├─ .github/workflows/pulls.yml        # cloud cron, one job per slot
├─ .python-version                    # 3.11
├─ requirements.txt                   # pin everything; incl psycopg2-binary
├─ schema_postgres.sql                # prod schema
├─ scripts/
│  ├─ database.py                     # get_connection(), wrapper, init_db, upsert_sql
│  ├─ pull_schedule.py  pull_odds.py  pull_results.py  pull_features.py
│  ├─ run_slot.py                     # orchestrator
│  ├─ grader.py  clv.py  units.py     # platform services
│  ├─ notify.py
│  ├─ migrate_to_postgres.py  verify_migration.py
│  └─ smoke_test.py  verify_auth.py
├─ bots/
│  ├─ base.py        # Bot, Pick, BotContext
│  ├─ registry.py
│  ├─ config.py      # per-bot thresholds (or a bot_config table)
│  ├─ bot_a.py  bot_b.py  bot_c.py     # your 3 bots — register() each
└─ dashboard/
   ├─ app.py
   ├─ components/ (auth.py, db bootstrap, styles, metrics, formatters)
   └─ pages/ (Today, Performance, My Bets, Settings)  # each gated
```

---

## 14. What to deliberately drop or redo

- **All MLB-specific tables/markets** (linescores/F5/YRFI/NRFI, pitcher/team stat
  tables) → replaced by the generic `results.detail_json` and `features` bag.
- **The two hardcoded engines** → the bot registry.
- **`algo`/`classification_version` columns** → `bot_key` / `config_version`.
- **Any single-axis confidence tiering** → dual-axis, per-bot config.
- Revisit **odds provider & markets** for football (spreads matter far more than
  in baseball; the platform already carries `market`/`line`, so it's mostly a
  bot + ingestion concern).

---

*Platform patterns are proven; the algorithms are yours to reinvent. Build the
connection abstraction and the bot registry first — everything else hangs off
those two.*
