"""Preflight tests for import_borrow_supabase.py.

These run without a database: they exercise the checks that stand between the
collector's SQLite file and Supabase. The load-and-verify path is integration
tested against a real Postgres (see the PR description), since an in-memory
stand-in would not exercise COPY, timestamptz parsing or RLS.
"""
import sqlite3
import tempfile
import unittest
from contextlib import redirect_stdout, redirect_stderr
from io import StringIO
from pathlib import Path

import import_borrow_supabase as imp

DDL = """
CREATE TABLE borrow (con_id INTEGER NOT NULL, feed_pull_id INTEGER NOT NULL,
  feed_ts TEXT NOT NULL, fee REAL, rebate REAL, available INTEGER,
  available_gt INTEGER NOT NULL DEFAULT 0, present INTEGER NOT NULL DEFAULT 1,
  PRIMARY KEY (con_id, feed_pull_id)) WITHOUT ROWID;
CREATE TABLE borrow_daily (symbol TEXT NOT NULL, country TEXT NOT NULL, date TEXT NOT NULL,
  fee REAL, open_fee REAL, high_fee REAL, low_fee REAL, rebate REAL, open_rebate REAL,
  high_rebate REAL, low_rebate REAL, available INTEGER, open_available REAL,
  high_available REAL, low_available REAL, source TEXT NOT NULL DEFAULT 'iborrowdesk',
  PRIMARY KEY (symbol, country, date)) WITHOUT ROWID;
CREATE TABLE feed_pull (id INTEGER PRIMARY KEY, country TEXT NOT NULL, feed_ts TEXT NOT NULL,
  md5 TEXT NOT NULL, row_count INTEGER NOT NULL, changed_rows INTEGER NOT NULL,
  fetched_at_utc TEXT NOT NULL, UNIQUE (country, md5));
CREATE TABLE latest (con_id INTEGER PRIMARY KEY, feed_ts TEXT NOT NULL, fee REAL, rebate REAL,
  available INTEGER, available_gt INTEGER NOT NULL DEFAULT 0, present INTEGER NOT NULL DEFAULT 1);
CREATE TABLE poll_attempt (id INTEGER PRIMARY KEY, country TEXT NOT NULL,
  attempted_at_utc TEXT NOT NULL, ok INTEGER NOT NULL, detail TEXT);
CREATE TABLE symbol (con_id INTEGER PRIMARY KEY, symbol TEXT NOT NULL, country TEXT NOT NULL,
  currency TEXT, name TEXT, isin TEXT, figi TEXT, first_seen TEXT NOT NULL, last_seen TEXT NOT NULL);
"""


def make_db(path: Path) -> sqlite3.Connection:
    con = sqlite3.connect(path)
    con.executescript(DDL)
    con.execute("INSERT INTO feed_pull VALUES (1,'usa','2026-10-02T12:47:27','m1',2,2,"
                "'2026-10-02T16:54:01+00:00')")
    con.execute("INSERT INTO symbol VALUES (7,'ABC','usa','USD','Abc','x','y',"
                "'2026-10-02T12:47:27','2026-10-02T12:47:27')")
    con.execute("INSERT INTO borrow VALUES (7,1,'2026-10-02T12:47:27',0.46,3.1,600000,0,1)")
    con.execute("INSERT INTO latest VALUES (7,'2026-10-02T12:47:27',0.46,3.1,600000,0,1)")
    con.execute("INSERT INTO poll_attempt VALUES (1,'usa','2026-10-02T16:54:01+00:00',1,'ok')")
    con.execute("INSERT INTO borrow_daily VALUES ('ABC','usa','2026-08-14',.4,.4,.4,.4,"
                "3.9,3.9,3.9,3.9,5300000,4.1e6,5.3e6,4.1e6,'iborrowdesk')")
    con.commit()
    return con


class PreflightTests(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.TemporaryDirectory()
        self.path = Path(self.dir.name) / "borrow.db"
        self.con = make_db(self.path)

    def tearDown(self):
        self.con.close()
        self.dir.cleanup()

    def run_preflight(self):
        out, err = StringIO(), StringIO()
        with redirect_stdout(out), redirect_stderr(err):
            imp.preflight(self.con)
        return out.getvalue() + err.getvalue()

    def assert_aborts(self, needle):
        with redirect_stdout(StringIO()), redirect_stderr(StringIO()) as err:
            with self.assertRaises(SystemExit):
                imp.preflight(self.con)
        self.assertIn(needle, err.getvalue())

    def test_clean_source_passes(self):
        out = self.run_preflight()
        self.assertIn("schema matches", out)
        self.assertIn("every borrow row references an existing feed version", out)

    def test_orphan_feed_pull_aborts(self):
        self.con.execute("INSERT INTO borrow VALUES (7,99,'2026-10-02T12:47:27',1,1,1,0,1)")
        self.assert_aborts("feed_pull id that does not exist")

    def test_unknown_column_aborts_rather_than_dropping_data(self):
        self.con.execute("ALTER TABLE borrow ADD COLUMN venue TEXT")
        self.assert_aborts("venue")

    def test_missing_column_aborts(self):
        self.con.executescript("DROP TABLE latest; CREATE TABLE latest (con_id INTEGER PRIMARY KEY,"
                               " feed_ts TEXT NOT NULL)")
        self.assert_aborts("latest")

    def test_naive_timestamp_must_be_iso_seconds(self):
        self.con.execute("UPDATE feed_pull SET feed_ts='2026-10-02 12:47' WHERE id=1")
        self.assert_aborts("feed_pull.feed_ts")

    def test_utc_column_must_carry_offset(self):
        self.con.execute("UPDATE poll_attempt SET attempted_at_utc='2026-10-02T16:54:01' WHERE id=1")
        self.assert_aborts("poll_attempt.attempted_at_utc")

    def test_missing_symbol_warns_but_continues(self):
        self.con.execute("INSERT INTO borrow VALUES (8,1,'2026-10-02T12:47:27',1,1,1,0,1)")
        self.assertIn("1 con_ids in borrow have no symbol row", self.run_preflight())


class ConversionTests(unittest.TestCase):
    def test_naive_times_are_tagged_eastern_for_postgres(self):
        self.assertEqual(imp.convert("2026-10-03T12:47:27", "et"),
                         "2026-10-03T12:47:27 America/New_York")

    def test_utc_times_pass_through(self):
        self.assertEqual(imp.convert("2026-10-03T16:54:01+00:00", "utc"),
                         "2026-10-03T16:54:01+00:00")

    def test_flags_become_booleans_and_nulls_survive(self):
        self.assertIs(imp.convert(0, "bool"), False)
        self.assertIs(imp.convert(1, "bool"), True)
        self.assertIsNone(imp.convert(None, "float"))
        self.assertIsNone(imp.convert(None, "et"))


if __name__ == "__main__":
    unittest.main()
