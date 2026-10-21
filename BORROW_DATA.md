# IBKR borrow history

Interactive Brokers publishes its shortable-stock feed (fee, rebate, shares
available) as a **current snapshot only**. It keeps no history and offers none
for download. The tables described here are this project's own record of that
feed over time, and **they are the only copy of that history anywhere**. Any
window nobody recorded is permanently lost.

Rules that protect the data:

- **One writer.** Exactly one collector writes these tables. `ibkr_borrow` is
  delta-encoded against the previous capture, so two writers corrupt it.
- **Append-only.** Nothing should update or delete rows in `ibkr_borrow` or
  `ibkr_feed_pull`.
- **IDs are permanent.** `ibkr_feed_pull.id` values were carried over from the
  original SQLite collector. Every borrow row points at one. Never renumber them.

## Coverage

| Source | Span | Breadth | Cadence |
|---|---|---|---|
| IBKR feed, collected (`ibkr_borrow`) | 2026-08-13 → ongoing | ~19.9K contracts | every 15 min (to be slowed; see below) |
| iborrowdesk backfill (`ibkr_borrow_daily`) | 2025-08-15 → 2026-08-14 | 3,644 symbols (~3.3K of ~3.5K names ≥ $300mm) | daily bars |

**Known gaps in collection** (no poll attempted, so anything IBKR published
then is lost):

- 2026-08-14 to 08-15, 23.4h total across four windows. This was the
  laptop-to-desktop migration.
- 2026-10-03 from about 18:00 UTC: repeated `500 OOPS: cannot change directory`
  errors from IBKR's FTP server (a fault on IBKR's side; Saturday, markets closed).
- The Supabase migration weekend, whatever window the collector is paused for.

Run `python borrow_health.py` for the full, current gap report. It reads
`ibkr_poll_attempt`, which records every poll, successful or not.

**Cadence change.** Polling ran at 15 minutes from the start and is being
slowed deliberately. Measures that depend on resolution change at that date
(how often a fee changes, intraday volatility of borrow cost). Compare like
with like across it. Record the date here when it happens: **____**.

**Feed versions ≠ data changes.** IBKR republishes the file, with a new md5,
even when no rows change. Weekend captures typically show `changed_rows = 0`.
So the number of feed versions overstates how often borrow terms actually move.

## Reading it correctly

### `ibkr_borrow` is delta-encoded

A row exists for a contract **only when something changed**: fee, rebate or
availability moved, or the contract entered or left the feed. A contract whose
fee was flat all week has **no rows for that week**.

So a plain date filter gives wrong answers:

```sql
-- WRONG: returns nothing for contracts whose fee didn't change that day
select * from ibkr_borrow where feed_ts::date = '2026-09-15';
```

Use the point-in-time function, which takes each contract's most recent
observation at or before the instant you name:

```sql
-- Borrow terms for everything in the feed at 4pm Eastern on 15 Sep
select * from ibkr_borrow_asof('2026-09-15 16:00 America/New_York');

-- One name's state at that moment
select * from ibkr_borrow_asof('2026-09-15 16:00 America/New_York')
where symbol = 'GME';

-- Full change history for one name (this IS a correct use of the raw table)
select b.feed_ts, b.fee, b.rebate, b.available, b.present
from ibkr_borrow b join ibkr_symbol s using (con_id)
where s.symbol = 'GME' order by b.feed_pull_id;
```

`ibkr_borrow_asof` scans the table, so allow a couple of seconds per call.
For a daily panel across many dates, build a snapshot table once rather than
calling it in a loop.

### `present = false` means the contract left the feed

When a contract disappears from IBKR's shortable list, a row is written with
`present = false` and null fee, rebate and availability. It is an exit marker,
not a missing value, and `ibkr_borrow_asof` excludes such contracts. If the
contract returns later, a normal row with `present = true` follows.

### Units

| Column | Meaning |
|---|---|
| `fee` | Annualised borrow fee in **percent**: `0.46` = 0.46%/yr, `54.26` = 54%/yr |
| `rebate` | Annualised rebate in percent. **Can be negative** on hard-to-borrow names (e.g. fee 54.26, rebate −50.38) |
| `available` | Shares available to borrow |
| `available_gt` | True when IBKR reported more than this (a `>` value); `available` is then a floor, not a count |

### Time

Every timestamp is `timestamptz`. The original collector stored IBKR's publish
times as naive **US/Eastern** text; the import converted them with Postgres's
timezone database, DST included. When writing new rows, store timezone-aware
values. Never write naive Eastern times again.

### `ibkr_borrow_daily` is keyed differently

The daily backfill is keyed by **ticker** (`symbol`), not IBKR `con_id`, and
comes from iborrowdesk rather than our own collection. To join it to the
collected data, go through `ibkr_symbol.symbol`. A handful of tickers map to
more than one `con_id`.

## Tables

| Table | Rows (at migration) | What it is |
|---|---|---|
| `ibkr_borrow` | ~2.9M | Delta-encoded observations. **Irreplaceable.** |
| `ibkr_feed_pull` | ~4.5K | One row per distinct feed version captured. **Irreplaceable.** |
| `ibkr_poll_attempt` | ~4.8K | Every poll, ok or failed. The record of when we were looking. |
| `ibkr_symbol` | ~21K | `con_id` → ticker, name, ISIN, FIGI; first and last seen |
| `ibkr_latest` | ~21K | Current state per contract (the collector's working state) |
| `ibkr_borrow_daily` | ~906K | iborrowdesk daily bars, Aug 2025 – Aug 2026 |

Short-interest data is **not** here: `finra_short_interest` is canonical.

## Access

The anonymous (`anon`) key can read nothing in these tables, unlike the FINRA
data. This history is the project's own asset. Signed-in users (`authenticated`)
can read everything and write nothing. Only the collector, connecting with the
database URL, writes.

To give a collaborator access, create a Supabase user for them in the
dashboard (Authentication → Users), or provision a read-only Postgres role for
SQL clients. To make the data public, add `anon` select policies mirroring
`supabase/migrations/0001_finra_short_interest.sql`.
