#!/usr/bin/env python3
"""Collection-health report for the IBKR borrow poller.

IBKR publishes only a current snapshot — no history. The poller's own
observations in `borrow` and `feed_pull` are therefore the ONLY record that
will ever exist, and any window nobody polled is gone permanently. No later
backfill, vendor, or re-request can recover it.

That makes the headline number not "how many rows do we have" but "when were
we not looking". This report is built around that:

  * A gap in `feed_pull` is usually fine. IBKR republishes only a handful of
    times a day, so long stretches with no new feed version are normal.
  * A gap in `poll_attempt` is the real signal. It means the collector was
    down, and anything IBKR published in that window is unrecoverable.

So the two are read separately, and the verdict is driven by polling gaps.

Read-only: opens the database with mode=ro and never writes, so it is safe to
run while the poller is mid-cycle.

Usage
  python borrow_health.py
  python borrow_health.py --db C:\\path\\to\\borrow.db --gap-hours 1.5
  python borrow_health.py --days 30          # restrict the window
"""

from __future__ import annotations

import argparse
import sqlite3
import sys
from collections import Counter
from datetime import datetime, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parent

# The poller is registered to run every 15 minutes.
POLL_INTERVAL_MIN = 15
EXPECTED_PER_DAY = 24 * 60 // POLL_INTERVAL_MIN      # 96

# Below this, a quiet stretch is just IBKR not republishing, not an outage.
DEFAULT_GAP_HOURS = 1.5


def resolve_db(explicit: str | None) -> Path:
    import os
    if explicit:
        return Path(explicit)
    if os.environ.get("BORROW_DB"):
        return Path(os.environ["BORROW_DB"])
    if os.environ.get("BORROW_DATA_ROOT"):
        return Path(os.environ["BORROW_DATA_ROOT"]) / "borrow.db"
    return ROOT / "borrow-data" / "borrow.db"


def connect(p: Path) -> sqlite3.Connection:
    if not p.exists():
        raise SystemExit(
            f"ERROR: {p} not found.\n"
            f"Pass --db, or set $BORROW_DB / $BORROW_DATA_ROOT."
        )
    return sqlite3.connect(f"file:{p.as_posix()}?mode=ro", uri=True)


def parse_ts(s: str | None):
    if not s:
        return None
    s = s.strip().replace("Z", "+00:00")
    try:
        dt = datetime.fromisoformat(s)
    except ValueError:
        return None
    return dt.replace(tzinfo=None)      # compare naively; all one feed clock


def fmt_dur(hours: float) -> str:
    if hours >= 48:
        return f"{hours/24:.1f}d"
    return f"{hours:.1f}h"


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="Borrow collection health")
    ap.add_argument("--db", default=None)
    ap.add_argument("--gap-hours", type=float, default=DEFAULT_GAP_HOURS,
                    help=f"report polling gaps longer than this (default {DEFAULT_GAP_HOURS})")
    ap.add_argument("--days", type=int, default=None, help="only the last N days")
    ap.add_argument("--top", type=int, default=12, help="how many gaps to list")
    args = ap.parse_args(argv)

    db = resolve_db(args.db)
    con = connect(db)
    cur = con.cursor()

    print("=" * 72)
    print(f"BORROW COLLECTION HEALTH — {db.name}  ({db.stat().st_size/1e6:.0f} MB)")
    print("=" * 72)

    def count(tbl):
        try:
            return cur.execute(f'SELECT COUNT(*) FROM "{tbl}"').fetchone()[0]
        except sqlite3.Error:
            return None

    # ---------------------------------------------------------- attempts
    try:
        rows = cur.execute(
            "SELECT attempted_at_utc, ok, detail FROM poll_attempt ORDER BY attempted_at_utc"
        ).fetchall()
    except sqlite3.Error:
        print("ERROR: no poll_attempt table — cannot assess collection gaps.",
              file=sys.stderr)
        return 2

    attempts = [(parse_ts(a), ok, d) for a, ok, d in rows]
    attempts = [a for a in attempts if a[0]]
    if not attempts:
        print("ERROR: poll_attempt is empty — nothing has been collected.", file=sys.stderr)
        return 3

    if args.days:
        cutoff = attempts[-1][0] - timedelta(days=args.days)
        attempts = [a for a in attempts if a[0] >= cutoff]

    first, last = attempts[0][0], attempts[-1][0]
    span_h = (last - first).total_seconds() / 3600
    span_d = span_h / 24
    ok_n = sum(1 for a in attempts if a[1])
    fail_n = len(attempts) - ok_n

    print(f"\nWindow:   {first:%Y-%m-%d %H:%M}  ->  {last:%Y-%m-%d %H:%M}   "
          f"({span_d:.1f} days)")
    age_h = (datetime.utcnow() - last).total_seconds() / 3600
    state = "LIVE" if age_h < 1 else "STALE" if age_h < 24 else "STOPPED"
    print(f"Last poll: {fmt_dur(age_h)} ago   -> collector looks {state}")

    print("\nStored observations")
    for tbl, note in [("borrow", "delta-encoded observations  <- IRREPLACEABLE"),
                      ("feed_pull", "distinct IBKR feed versions <- IRREPLACEABLE"),
                      ("poll_attempt", "poll attempts"),
                      ("symbol", "contracts seen"),
                      ("borrow_daily", "backfilled daily bars (refetchable)")]:
        n = count(tbl)
        print(f"  {tbl:<14}{'(missing)' if n is None else format(n, ',') :>12}   {note}")

    print(f"\nPolling   ok {ok_n:,} / failed {fail_n:,}"
          + (f"  ({fail_n/max(len(attempts),1):.1%} failure rate)" if fail_n else ""))
    if fail_n:
        reasons = Counter(str(d)[:48] for t, ok, d in attempts if not ok)
        for r, n in reasons.most_common(5):
            print(f"    {n:>5,}  {r}")

    # ------------------------------------------------- gaps (the real thing)
    gaps = []
    for i in range(1, len(attempts)):
        dt_h = (attempts[i][0] - attempts[i-1][0]).total_seconds() / 3600
        if dt_h > args.gap_hours:
            gaps.append((attempts[i-1][0], attempts[i][0], dt_h))

    gap_total = sum(g[2] for g in gaps)
    print(f"\nCOLLECTION GAPS (> {args.gap_hours}h with no poll attempted)")
    print("  Anything IBKR published inside these windows is gone for good.")
    if not gaps:
        print("  None. Unbroken coverage across the window.")
    else:
        gaps.sort(key=lambda g: -g[2])
        for a, b, h in gaps[: args.top]:
            print(f"    {a:%Y-%m-%d %H:%M} -> {b:%Y-%m-%d %H:%M}   {fmt_dur(h):>7}")
        if len(gaps) > args.top:
            print(f"    ... and {len(gaps)-args.top} more")
        print(f"  {len(gaps)} gap(s), {fmt_dur(gap_total)} total "
              f"({gap_total/max(span_h,1e-9):.1%} of the window)")

    # ---------------------------------------------------- per-day coverage
    per_day = Counter(a[0].date() for a in attempts)
    all_days = [(first.date() + timedelta(days=k))
                for k in range((last.date() - first.date()).days + 1)]
    dead = [d for d in all_days if per_day.get(d, 0) == 0]
    thin = [d for d in all_days
            if 0 < per_day.get(d, 0) < EXPECTED_PER_DAY * 0.9]
    print(f"\nDaily coverage (target {EXPECTED_PER_DAY} polls/day)")
    print(f"  full days:        {len(all_days) - len(dead) - len(thin):,} / {len(all_days):,}")
    print(f"  thin (<90%):      {len(thin):,}" + (f"   {', '.join(str(d) for d in thin[:6])}" if thin else ""))
    print(f"  ZERO polls:       {len(dead):,}" + (f"   {', '.join(str(d) for d in dead[:6])}" if dead else ""))

    # ------------------------------------------------------- feed capture
    try:
        fp = cur.execute("SELECT MIN(feed_ts), MAX(feed_ts), COUNT(*) FROM feed_pull").fetchone()
        lo, hi, n = parse_ts(fp[0]), parse_ts(fp[1]), fp[2]
        if lo and hi and n:
            per_day_feeds = n / max((hi - lo).total_seconds() / 86400, 1e-9)
            print(f"\nFeed versions captured: {n:,}  ({per_day_feeds:.1f}/day)")
            print(f"  {lo:%Y-%m-%d %H:%M} -> {hi:%Y-%m-%d %H:%M}")
            print("  (IBKR republishes only a few times daily; a quiet stretch here is")
            print("   normal — the polling gaps above are what indicate real loss.)")
    except sqlite3.Error:
        pass

    # ------------------------------------------------------------ backups
    print("\nBackups")
    cands = []
    for d in {db.parent, db.parent/"backups", db.parent.parent/"backups"}:
        if d.is_dir():
            cands += [p for p in d.glob("*.db") if p.resolve() != db.resolve()]
    if cands:
        cands.sort(key=lambda p: p.stat().st_mtime, reverse=True)
        newest = datetime.fromtimestamp(cands[0].stat().st_mtime)
        age_d = (datetime.now() - newest).days
        print(f"  {len(cands)} copy/copies found; newest {newest:%Y-%m-%d} ({age_d}d old)")
        if age_d > 2:
            print("  WARNING: newest backup is stale.")
    else:
        print("  NONE FOUND next to the database.")
        print("  This database is the only copy of data that cannot be re-collected.")

    # ------------------------------------------------------------ verdict
    print("\n" + "=" * 72)
    hist_days = span_d - (gap_total / 24)
    print(f"VERDICT: {fmt_dur(span_h)} of wall-clock, {hist_days:.1f} days effectively covered.")
    if state == "STOPPED":
        print("  !! Collector appears STOPPED. Every hour it stays down is permanent loss.")
    elif dead:
        print(f"  !! {len(dead)} day(s) with no polling at all — unrecoverable.")
    elif gaps:
        print("  Minor gaps only; the series is usable as history.")
    else:
        print("  Clean, unbroken collection.")
    print("=" * 72)
    con.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
