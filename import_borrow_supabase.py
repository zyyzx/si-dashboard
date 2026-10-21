#!/usr/bin/env python3
"""One-time import of the IBKR borrow collector's SQLite database into Supabase.

The collector's borrow and feed_pull tables are the only record of past IBKR
borrow costs that will ever exist (IBKR publishes no history), so this script
is built to fail loudly rather than import something subtly wrong:

  1. Snapshot.  Copies borrow.db with SQLite's online backup API into a
     consistent point-in-time file and imports from THAT, never the live file.
     A poll landing mid-import therefore cannot produce orphaned rows, the
     parity check compares against exactly what was imported, and the
     snapshot doubles as a cold archive taken at the moment of migration.

  2. Preflight.  Checks the schema is the one this script was written for,
     every borrow row references an existing feed version, and every
     timestamp is in the expected form — before touching the database.

  3. Load.  One transaction, COPY per table, in foreign-key order.
     * feed_pull.id and poll_attempt.id are carried over unchanged. Every
       borrow row points at a feed_pull id; regenerating them would silently
       attach 2.9M observations to the wrong feed versions.
     * Naive timestamps are IBKR's US/Eastern and are converted by Postgres
       with its own timezone database ("... America/New_York"), so DST is
       handled server-side and no Windows tzdata package is needed. *_utc
       columns are already UTC and pass through.

  4. Verify, then commit.  Row counts per table, per-feed-version row counts
     and fee sums for ibkr_borrow, and an exact timezone spot-check all run
     inside the transaction. Any mismatch rolls the whole import back.

Not migrated, by design: si_history, short_interest, float_shares and
market_cap. finra_short_interest is canonical for short interest here.

Apply supabase/migrations/0003_ibkr_borrow.sql first. Pause the collector's
scheduled task before running, or rows it writes after the snapshot will not
be migrated.

Usage
  python import_borrow_supabase.py --dry-run          # snapshot + preflight only
  python import_borrow_supabase.py                    # import (needs SUPABASE_DB_URL)
  python import_borrow_supabase.py --snapshot path.db # import from an existing copy
  python import_borrow_supabase.py --replace          # empty the ibkr_* tables first
"""

from __future__ import annotations

import argparse
import math
import os
import re
import sqlite3
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent
EASTERN = "America/New_York"

NAIVE_TS = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}$")
UTC_TS = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(\.\d+)?\+00:00$")
DATE = re.compile(r"^\d{4}-\d{2}-\d{2}$")

# (sqlite table, postgres table, [(sqlite col, postgres col, kind)])
# kind: int | float | text | bool | et (naive Eastern) | utc | date
TABLES = [
    ("symbol", "ibkr_symbol", [
        ("con_id", "con_id", "int"), ("symbol", "symbol", "text"),
        ("country", "country", "text"), ("currency", "currency", "text"),
        ("name", "name", "text"), ("isin", "isin", "text"), ("figi", "figi", "text"),
        ("first_seen", "first_seen", "et"), ("last_seen", "last_seen", "et"),
    ]),
    ("feed_pull", "ibkr_feed_pull", [
        ("id", "id", "int"), ("country", "country", "text"),
        ("feed_ts", "feed_ts", "et"), ("md5", "md5", "text"),
        ("row_count", "row_count", "int"), ("changed_rows", "changed_rows", "int"),
        ("fetched_at_utc", "fetched_at", "utc"),
    ]),
    ("borrow", "ibkr_borrow", [
        ("con_id", "con_id", "int"), ("feed_pull_id", "feed_pull_id", "int"),
        ("feed_ts", "feed_ts", "et"), ("fee", "fee", "float"),
        ("rebate", "rebate", "float"), ("available", "available", "int"),
        ("available_gt", "available_gt", "bool"), ("present", "present", "bool"),
    ]),
    ("latest", "ibkr_latest", [
        ("con_id", "con_id", "int"), ("feed_ts", "feed_ts", "et"),
        ("fee", "fee", "float"), ("rebate", "rebate", "float"),
        ("available", "available", "int"), ("available_gt", "available_gt", "bool"),
        ("present", "present", "bool"),
    ]),
    ("poll_attempt", "ibkr_poll_attempt", [
        ("id", "id", "int"), ("country", "country", "text"),
        ("attempted_at_utc", "attempted_at", "utc"), ("ok", "ok", "bool"),
        ("detail", "detail", "text"),
    ]),
    ("borrow_daily", "ibkr_borrow_daily", [
        ("symbol", "symbol", "text"), ("country", "country", "text"),
        ("date", "date", "date"), ("fee", "fee", "float"),
        ("open_fee", "open_fee", "float"), ("high_fee", "high_fee", "float"),
        ("low_fee", "low_fee", "float"), ("rebate", "rebate", "float"),
        ("open_rebate", "open_rebate", "float"), ("high_rebate", "high_rebate", "float"),
        ("low_rebate", "low_rebate", "float"), ("available", "available", "int"),
        ("open_available", "open_available", "float"),
        ("high_available", "high_available", "float"),
        ("low_available", "low_available", "float"), ("source", "source", "text"),
    ]),
]
NOT_MIGRATED = ["si_history", "short_interest", "float_shares", "market_cap"]
SEQUENCES = [("ibkr_feed_pull", "ibkr_feed_pull_id_seq"),
             ("ibkr_poll_attempt", "ibkr_poll_attempt_id_seq")]


# ------------------------------------------------------------------ helpers

def resolve_db(explicit: str | None) -> Path:
    if explicit:
        return Path(explicit)
    if os.environ.get("BORROW_DB"):
        return Path(os.environ["BORROW_DB"])
    if os.environ.get("BORROW_DATA_ROOT"):
        return Path(os.environ["BORROW_DATA_ROOT"]) / "borrow.db"
    return ROOT / "borrow-data" / "borrow.db"


def fail(msg: str) -> "None":
    print(f"\nABORT: {msg}", file=sys.stderr)
    sys.exit(2)


def take_snapshot(live: Path) -> Path:
    """Consistent point-in-time copy via the SQLite online backup API."""
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    snap = live.with_name(f"borrow_premigration_{stamp}.db")
    src = sqlite3.connect(f"file:{live.as_posix()}?mode=ro", uri=True)
    dst = sqlite3.connect(snap)
    t0 = time.time()
    with dst:
        src.backup(dst)
    src.close()
    dst.close()
    print(f"Snapshot: {snap.name}  ({snap.stat().st_size/1e6:.0f} MB, {time.time()-t0:.0f}s)")
    return snap


def convert(v, kind: str):
    if v is None:
        return None
    if kind == "int":
        return int(v)
    if kind == "float":
        return float(v)
    if kind == "bool":
        return bool(int(v))
    if kind == "et":
        return f"{v} {EASTERN}"      # Postgres resolves the zone, DST included
    return v                          # text, utc, date: pass through


# ---------------------------------------------------------------- preflight

def preflight(con: sqlite3.Connection) -> dict[str, int]:
    cur = con.cursor()
    print("\nPreflight")

    have = {r[0] for r in cur.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    for st, _, cols in TABLES:
        if st not in have:
            fail(f"table '{st}' missing from the SQLite file")
        actual = {r[1] for r in cur.execute(f'PRAGMA table_info("{st}")')}
        expected = {c[0] for c in cols}
        if expected - actual:
            fail(f"'{st}' lacks columns {sorted(expected - actual)} — schema changed; "
                 f"update this script before importing")
        extra = actual - expected
        if extra:
            fail(f"'{st}' has columns this script does not know: {sorted(extra)}. "
                 f"Importing would drop them silently; update the script and migration.")
    print("  schema matches")

    counts = {st: cur.execute(f'SELECT COUNT(*) FROM "{st}"').fetchone()[0]
              for st, _, _ in TABLES}
    for st, n in counts.items():
        print(f"  {st:<14}{n:>12,}")

    orphan_fp = cur.execute(
        "SELECT COUNT(*) FROM borrow b LEFT JOIN feed_pull f ON f.id = b.feed_pull_id "
        "WHERE f.id IS NULL").fetchone()[0]
    if orphan_fp:
        fail(f"{orphan_fp:,} borrow rows reference a feed_pull id that does not exist")
    print("  every borrow row references an existing feed version")

    orphan_sym = cur.execute(
        "SELECT COUNT(DISTINCT b.con_id) FROM borrow b LEFT JOIN symbol s "
        "ON s.con_id = b.con_id WHERE s.con_id IS NULL").fetchone()[0]
    if orphan_sym:
        print(f"  WARNING: {orphan_sym:,} con_ids in borrow have no symbol row "
              f"(imported anyway; they will show symbol = null in ibkr_borrow_asof)")

    dupe = cur.execute("SELECT COUNT(*) - COUNT(DISTINCT md5) FROM feed_pull").fetchone()[0]
    if dupe:
        fail(f"{dupe} duplicate md5 values in feed_pull would violate unique(country, md5)")

    checks = [
        ("symbol", "first_seen", NAIVE_TS), ("symbol", "last_seen", NAIVE_TS),
        ("feed_pull", "feed_ts", NAIVE_TS), ("feed_pull", "fetched_at_utc", UTC_TS),
        ("borrow", "feed_ts", NAIVE_TS), ("latest", "feed_ts", NAIVE_TS),
        ("poll_attempt", "attempted_at_utc", UTC_TS), ("borrow_daily", "date", DATE),
    ]
    for tbl, col, pat in checks:
        bad = 0
        sample = None
        for (v,) in cur.execute(f'SELECT DISTINCT "{col}" FROM "{tbl}"'):
            if v is None or not pat.match(str(v)):
                bad += 1
                sample = sample or v
        if bad:
            fail(f"{bad:,} unexpected values in {tbl}.{col}, e.g. {sample!r}")
    print("  all timestamps in the expected form (naive = Eastern, *_utc = UTC)")

    # The one hour a year where a naive Eastern time is genuinely ambiguous.
    amb = 0
    for (v,) in cur.execute("SELECT DISTINCT feed_ts FROM feed_pull"):
        d = datetime.fromisoformat(v)
        if d.month == 11 and d.weekday() == 6 and d.day <= 7 and d.hour == 1:
            amb += 1
    if amb:
        print(f"  WARNING: {amb} feed timestamps fall in a DST fall-back hour; Postgres "
              f"resolves those to standard time")

    last = cur.execute("SELECT MAX(attempted_at_utc) FROM poll_attempt").fetchone()[0]
    if last:
        age_min = (datetime.now(timezone.utc)
                   - datetime.fromisoformat(last)).total_seconds() / 60
        if age_min < 30:
            print(f"  NOTE: last poll was {age_min:.0f} min before the snapshot. If the "
                  f"collector is still scheduled, anything it writes from now on is "
                  f"not in this import — pause the task first.")
    return counts


# --------------------------------------------------------------------- load

def load(con, pg, counts: dict[str, int]) -> None:
    cur = con.cursor()
    for st, pt, cols in TABLES:
        scols = ", ".join(f'"{c[0]}"' for c in cols)
        pcols = ", ".join(c[1] for c in cols)
        kinds = [c[2] for c in cols]
        t0 = time.time()
        n = 0
        with pg.cursor() as pc:
            with pc.copy(f"COPY public.{pt} ({pcols}) FROM STDIN") as cp:
                for row in cur.execute(f'SELECT {scols} FROM "{st}"'):
                    cp.write_row([convert(v, k) for v, k in zip(row, kinds)])
                    n += 1
                    if n % 500_000 == 0:
                        print(f"    {pt}: {n:,} / {counts[st]:,}")
        print(f"  {pt:<20}{n:>12,} rows  ({time.time()-t0:.0f}s)")

    with pg.cursor() as pc:
        for tbl, seq in SEQUENCES:
            pc.execute(f"SELECT setval('public.{seq}', "
                       f"GREATEST((SELECT COALESCE(MAX(id), 0) FROM public.{tbl}), 1))")
            print(f"  sequence {seq} -> {pc.fetchone()[0]:,}")


# ------------------------------------------------------------------- verify

def verify(con, pg) -> None:
    cur = con.cursor()
    print("\nVerify (inside the transaction — any failure rolls everything back)")
    problems = []

    with pg.cursor() as pc:
        for st, pt, _ in TABLES:
            a = cur.execute(f'SELECT COUNT(*) FROM "{st}"').fetchone()[0]
            pc.execute(f"SELECT COUNT(*) FROM public.{pt}")
            b = pc.fetchone()[0]
            status = "ok" if a == b else "MISMATCH"
            if a != b:
                problems.append(f"{pt}: sqlite {a:,} vs postgres {b:,}")
            print(f"  {pt:<20} sqlite {a:>11,}   postgres {b:>11,}   {status}")

        # Per-feed-version count and fee sum: catches rows attached to the
        # wrong feed_pull_id, which a total count alone would not.
        src = {r[0]: (r[1], r[2] or 0.0, r[3]) for r in cur.execute(
            "SELECT feed_pull_id, COUNT(*), SUM(fee), SUM(1 - present) "
            "FROM borrow GROUP BY feed_pull_id")}
        pc.execute("SELECT feed_pull_id, COUNT(*), COALESCE(SUM(fee), 0), "
                   "SUM(CASE WHEN present THEN 0 ELSE 1 END) "
                   "FROM public.ibkr_borrow GROUP BY feed_pull_id")
        dst = {r[0]: (r[1], float(r[2]), r[3]) for r in pc.fetchall()}
        bad = [k for k in src.keys() | dst.keys()
               if k not in src or k not in dst
               or src[k][0] != dst[k][0] or src[k][2] != dst[k][2]
               or not math.isclose(src[k][1], dst[k][1], rel_tol=1e-9, abs_tol=1e-6)]
        if bad:
            problems.append(f"ibkr_borrow: {len(bad)} feed versions differ, e.g. id {sorted(bad)[0]}")
        print(f"  ibkr_borrow per-feed-version counts, fee sums, exits: "
              f"{'ok' if not bad else f'{len(bad)} MISMATCHES'} ({len(src):,} versions)")

        # Exact timezone spot-check on the newest feed version.
        fid, naive = cur.execute(
            "SELECT id, feed_ts FROM feed_pull ORDER BY id DESC LIMIT 1").fetchone()
        pc.execute("SELECT feed_ts = (%s)::timestamptz, "
                   "to_char(feed_ts AT TIME ZONE 'UTC', 'YYYY-MM-DD HH24:MI:SS') "
                   "FROM public.ibkr_feed_pull WHERE id = %s",
                   (f"{naive} {EASTERN}", fid))
        same, utc = pc.fetchone()
        if not same:
            problems.append(f"feed_pull {fid}: timezone conversion wrong")
        print(f"  timezone: feed {fid} '{naive}' Eastern -> {utc} UTC   "
              f"{'ok' if same else 'MISMATCH'}")

        pc.execute("SELECT (SELECT last_value FROM public.ibkr_feed_pull_id_seq) "
                   ">= (SELECT MAX(id) FROM public.ibkr_feed_pull)")
        if not pc.fetchone()[0]:
            problems.append("ibkr_feed_pull_id_seq is behind max(id)")

    if problems:
        print("\nVerification failed:")
        for p in problems:
            print(f"  - {p}")
        raise RuntimeError("verification failed")
    print("  all checks passed")


# --------------------------------------------------------------------- main

def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="Import borrow.db into Supabase")
    ap.add_argument("--db", help="live borrow.db (default: $BORROW_DB, ./borrow-data/borrow.db)")
    ap.add_argument("--snapshot", help="import from this existing copy instead of snapshotting")
    ap.add_argument("--dry-run", action="store_true", help="snapshot and preflight only")
    ap.add_argument("--replace", action="store_true",
                    help="empty the ibkr_* tables first (never touches other tables)")
    args = ap.parse_args(argv)

    print("=" * 72)
    print("IBKR BORROW -> SUPABASE IMPORT")
    print("=" * 72)
    print(f"Not migrated (canonical elsewhere): {', '.join(NOT_MIGRATED)}")

    if args.snapshot:
        snap = Path(args.snapshot)
        if not snap.exists():
            fail(f"{snap} not found")
        print(f"Using existing snapshot: {snap}")
    else:
        live = resolve_db(args.db)
        if not live.exists():
            fail(f"{live} not found; pass --db or set $BORROW_DB")
        snap = take_snapshot(live)

    con = sqlite3.connect(f"file:{snap.as_posix()}?mode=ro", uri=True)
    counts = preflight(con)

    if args.dry_run:
        print("\nDry run complete. Nothing written to Supabase.")
        print(f"Snapshot kept: {snap}")
        return 0

    url = os.environ.get("SUPABASE_DB_URL")
    if not url:
        fail("SUPABASE_DB_URL is not set")
    import psycopg
    kw = {} if "sslmode=" in url else {"sslmode": "require"}
    try:
        pg = psycopg.connect(url, connect_timeout=15, **kw)
    except psycopg.Error as e:
        # Never echo the URL: it carries the database password.
        fail(f"could not connect to Supabase ({type(e).__name__})")

    t0 = time.time()
    try:
        with pg.cursor() as pc:
            pc.execute("SELECT to_regclass('public.ibkr_borrow') IS NOT NULL")
            if not pc.fetchone()[0]:
                fail("ibkr_* tables not found — apply supabase/migrations/0003_ibkr_borrow.sql first")
            nonempty = []
            for _, pt, _ in TABLES:
                pc.execute(f"SELECT EXISTS (SELECT 1 FROM public.{pt})")
                if pc.fetchone()[0]:
                    nonempty.append(pt)
            if nonempty and not args.replace:
                fail(f"target tables already contain data: {', '.join(nonempty)}. "
                     f"Re-run with --replace to empty them first.")
            if nonempty:
                pc.execute("TRUNCATE " + ", ".join(f"public.{t[1]}" for t in TABLES))
                print(f"\nEmptied: {', '.join(nonempty)}")

        print("\nLoad")
        load(con, pg, counts)
        verify(con, pg)
        pg.commit()
    except BaseException:
        pg.rollback()
        print("\nRolled back. Supabase is unchanged.", file=sys.stderr)
        raise

    print(f"\nCommitted in {time.time()-t0:.0f}s.")
    with pg.cursor() as pc:
        print("\nStorage")
        for _, pt, _ in TABLES:
            pc.execute("SELECT pg_size_pretty(pg_total_relation_size(%s))", (f"public.{pt}",))
            print(f"  {pt:<20}{pc.fetchone()[0]:>12}")
        pc.execute("SELECT pg_size_pretty(pg_database_size(current_database()))")
        print(f"  {'whole database':<20}{pc.fetchone()[0]:>12}   <- check against your plan")
    pg.close()
    print(f"\nKeep the snapshot as the migration archive: {snap}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
