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

# Cadence is inferred from the last week of attempts rather than assumed:
# the poller started at 15 minutes but may be deliberately slowed, and a
# hardcoded 96/day target would then flag every healthy day as "thin".
DEFAULT_GAP_HOURS = None     # None -> max(1.5h, 2.5 x inferred interval)


def infer_interval_min(times) -> float:
    """Median spacing between consecutive attempts over the last 7 days."""
    if len(times) < 3:
        return 15.0
    cutoff = times[-1] - timedelta(days=7)
    recent = [t for t in times if t >= cutoff] or times
    deltas = sorted((recent[i] - recent[i-1]).total_seconds() / 60
                    for i in range(1, len(recent)))
    deltas = [d for d in deltas if d > 0] or [15.0]
    return deltas[len(deltas) // 2]


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
                    help="report polling gaps longer than this "
                         "(default: max(1.5h, 2.5x the inferred poll interval))")
    ap.add_argument("--interval-min", type=float, default=None,
                    help="expected poll interval; default is inferred from the last week")
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
    interval = args.interval_min or infer_interval_min([a[0] for a in attempts])
    expected_per_day = 1440.0 / interval
    if args.gap_hours is None:
        args.gap_hours = max(1.5, 2.5 * interval / 60)
    span_h = (last - first).total_seconds() / 3600
    span_d = span_h / 24
    ok_n = sum(1 for a in attempts if a[1])
    fail_n = len(attempts) - ok_n

    print(f"\nWindow:   {first:%Y-%m-%d %H:%M}  ->  {last:%Y-%m-%d %H:%M}   "
          f"({span_d:.1f} days)")
    age_h = (datetime.utcnow() - last).total_seconds() / 3600
    state = "LIVE" if age_h < 1 else "STALE" if age_h < 24 else "STOPPED"
    print(f"Last poll: {fmt_dur(age_h)} ago   -> collector looks {state}")
    print(f"Cadence:  every {interval:.0f} min "
          f"({'set' if args.interval_min else 'inferred from the last 7 days'})"
          f" -> {expected_per_day:.0f} polls/day expected")

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
    # First and last calendar days are partial by construction (collection
    # started mid-day; today isn't over), so judging them against a full
    # day's target would always flag them. Real outages on those days still
    # appear in the gap list above.
    whole = all_days[1:-1] if len(all_days) > 2 else []
    dead = [d for d in whole if per_day.get(d, 0) == 0]
    thin = [d for d in whole
            if 0 < per_day.get(d, 0) < expected_per_day * 0.9]
    print(f"\nDaily coverage (target {expected_per_day:.0f} polls/day)")
    print(f"  full days:        {len(whole) - len(dead) - len(thin):,} / {len(whole):,}"
          "   (first/last day excluded as partial)")
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
            ratio = per_day_feeds / max(expected_per_day, 1e-9)
            print(f"  A new feed version on {min(ratio,1):.0%} of polls.", end=" ")
            if ratio > 0.8:
                print("IBKR is updating at least as fast as you")
                print("  poll, so intermediate versions between polls are not captured. Fine")
                print("  if your analysis is coarser than the poll interval.")
            else:
                print("Polls often see an unchanged feed, so the")
                print("  interval is capturing most of what IBKR publishes.")
    except sqlite3.Error:
        pass

    # ------------------------------------------------------------ backups
    print("\nBackups")
    # The backup job's own log is authoritative: it says where it wrote,
    # and it verified row counts before promoting each copy. Guessing paths
    # beside the database missed C:\\Users\\<you>\\borrow-backups entirely and
    # mistook the migration transfer file for a stale backup.
    log = db.parent / "backup.log"
    if log.exists():
        lines = log.read_text(encoding="utf-8", errors="replace").splitlines()
        ok_lines = [l for l in lines if "backup ok" in l]
        dest = next((l.split("retained in", 1)[1].strip()
                     for l in reversed(lines) if "retained in" in l), None)
        errs = [l for l in lines[-200:] if " ERROR " in l or "FAILED" in l.upper()]
        same_vol = any("same volume" in l for l in lines[-50:])
        if ok_lines:
            last_ok = ok_lines[-1]
            ts = parse_ts(last_ok[:19])
            age = (datetime.now() - ts).total_seconds() / 86400 if ts else None
            print(f"  last verified backup: {last_ok[:19]}"
                  + (f"  ({age:.1f}d ago)" if age is not None else ""))
            print(f"    {last_ok[19:].strip()}")
            if age is not None and age > 2:
                print("  WARNING: no successful backup in over 2 days.")
        else:
            print("  backup.log has no successful run recorded.")
        if dest:
            print(f"  destination: {dest}")
            dp = Path(dest)
            if dp.is_dir():
                copies = sorted(dp.glob("*.db"), key=lambda p: p.stat().st_mtime)
                print(f"  {len(copies)} cop{'y' if len(copies)==1 else 'ies'} present on disk")
        if errs:
            print(f"  {len(errs)} error line(s) in recent log, latest:")
            print(f"    {errs[-1][:110]}")
        if same_vol:
            print("  NOTE: backups share a volume with the database - they survive")
            print("  deletion or corruption, but not a disk failure.")
    else:
        print("  No backup.log beside the database; cannot confirm backups.")

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
