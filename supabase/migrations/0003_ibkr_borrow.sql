-- IBKR borrow collector: the poller's own observations of the IBKR shortable feed.
--
-- IBKR publishes a current snapshot only, never history. ibkr_borrow and
-- ibkr_feed_pull are therefore the only record of past borrow costs that will
-- ever exist. Treat them as append-only; nothing should rewrite or delete rows.
--
-- Shape: ibkr_borrow is DELTA-ENCODED. A row is written for a contract only
-- when its fee / rebate / availability changed in a captured feed version, or
-- when it entered or left the feed (present = false, data columns null). A
-- contract whose fee did not change on a given day has NO row for that day.
-- Query point-in-time state with ibkr_borrow_asof(), never with a plain WHERE
-- on a date.
--
-- Times are timestamptz throughout. The SQLite source stored IBKR's feed
-- timestamps as naive US/Eastern text; the importer converts them, and new
-- writers must store UTC-aware values directly.
--
-- The SI tables that also live in the collector's SQLite file (si_history,
-- short_interest, float_shares, market_cap) are deliberately not migrated:
-- finra_short_interest is canonical for short interest in this project.

-- ---------------------------------------------------------------- tables

create table if not exists public.ibkr_symbol (
    con_id      bigint      primary key,      -- IBKR contract id
    symbol      text        not null,
    country     text        not null,
    currency    text,
    name        text,
    isin        text,
    figi        text,
    first_seen  timestamptz not null,
    last_seen   timestamptz not null
);
create index if not exists ibkr_symbol_symbol_idx on public.ibkr_symbol (symbol, country);

-- One row per distinct feed version captured. id values are carried over
-- from SQLite unchanged: every ibkr_borrow row references them, so they must
-- never be regenerated. The importer moves the sequence past max(id).
create sequence if not exists public.ibkr_feed_pull_id_seq;
create table if not exists public.ibkr_feed_pull (
    id            bigint      primary key default nextval('public.ibkr_feed_pull_id_seq'),
    country       text        not null,
    feed_ts       timestamptz not null,       -- IBKR's publish time
    md5           text        not null,       -- IBKR's published md5 (LF-normalised)
    row_count     integer     not null,
    changed_rows  integer     not null,
    fetched_at    timestamptz not null,
    unique (country, md5)
);
alter sequence public.ibkr_feed_pull_id_seq owned by public.ibkr_feed_pull.id;

create table if not exists public.ibkr_borrow (
    con_id        bigint      not null,
    feed_pull_id  bigint      not null references public.ibkr_feed_pull (id),
    feed_ts       timestamptz not null,       -- denormalised from ibkr_feed_pull
    fee           double precision,           -- annualised percent: 0.46 = 0.46%/yr
    rebate        double precision,           -- can be negative on hard-to-borrow names
    available     bigint,                     -- shares available to borrow
    available_gt  boolean     not null default false,  -- feed reported ">available"
    present       boolean     not null default true,   -- false = left the feed
    primary key (con_id, feed_pull_id)
);
-- "What changed in feed version X" without scanning every contract.
create index if not exists ibkr_borrow_feed_pull_idx on public.ibkr_borrow (feed_pull_id);

-- Current state per contract. Derivable from ibkr_borrow, but the collector
-- reads it each run to compute deltas, so it is operational state, not a cache.
create table if not exists public.ibkr_latest (
    con_id        bigint      primary key,
    feed_ts       timestamptz not null,
    fee           double precision,
    rebate        double precision,
    available     bigint,
    available_gt  boolean     not null default false,
    present       boolean     not null default true
);

-- Every poll, successful or not. This is the record of WHEN the collector was
-- looking; gaps here are windows whose IBKR publishes are permanently lost.
create sequence if not exists public.ibkr_poll_attempt_id_seq;
create table if not exists public.ibkr_poll_attempt (
    id            bigint      primary key default nextval('public.ibkr_poll_attempt_id_seq'),
    country       text        not null,
    attempted_at  timestamptz not null,
    ok            boolean     not null,
    detail        text
);
alter sequence public.ibkr_poll_attempt_id_seq owned by public.ibkr_poll_attempt.id;
create index if not exists ibkr_poll_attempt_time_idx on public.ibkr_poll_attempt (attempted_at);

-- Daily bars backfilled from iborrowdesk (Aug 2025 onward, ~3.6K symbols).
-- Keyed by ticker, not con_id. Refetchable in principle, but slowly.
create table if not exists public.ibkr_borrow_daily (
    symbol          text not null,
    country         text not null,
    date            date not null,
    fee             double precision,         -- daily close
    open_fee        double precision,
    high_fee        double precision,
    low_fee         double precision,
    rebate          double precision,
    open_rebate     double precision,
    high_rebate     double precision,
    low_rebate      double precision,
    available       bigint,
    open_available  double precision,
    high_available  double precision,
    low_available   double precision,
    source          text not null default 'iborrowdesk',
    primary key (symbol, country, date)
);
create index if not exists ibkr_borrow_daily_date_idx on public.ibkr_borrow_daily (date);

-- ------------------------------------------------------- point-in-time read

-- Borrow state for every contract that was in the feed at p_as_of: the most
-- recent observation at or before that instant, excluding contracts whose
-- most recent observation is their exit from the feed.
create or replace function public.ibkr_borrow_asof(p_as_of timestamptz)
returns table (
    con_id        bigint,
    symbol        text,
    observed_at   timestamptz,
    fee           double precision,
    rebate        double precision,
    available     bigint,
    available_gt  boolean
)
language sql stable
set search_path = public
as $$
    select l.con_id, s.symbol, l.feed_ts, l.fee, l.rebate, l.available, l.available_gt
    from (
        select distinct on (b.con_id) b.*
        from public.ibkr_borrow b
        where b.feed_ts <= p_as_of
        order by b.con_id, b.feed_pull_id desc
    ) l
    left join public.ibkr_symbol s on s.con_id = l.con_id
    where l.present
$$;

-- ----------------------------------------------------------------- access
-- Unlike finra_short_interest (public data, anon-readable), this collected
-- history is the project's own asset, so the anon key gets nothing. Logged-in
-- collaborators (role authenticated) can read; only the collector, connecting
-- with the database URL, writes. To make it public later, add anon policies
-- mirroring 0001.

alter table public.ibkr_symbol        enable row level security;
alter table public.ibkr_feed_pull     enable row level security;
alter table public.ibkr_borrow        enable row level security;
alter table public.ibkr_latest        enable row level security;
alter table public.ibkr_poll_attempt  enable row level security;
alter table public.ibkr_borrow_daily  enable row level security;

revoke all on public.ibkr_symbol, public.ibkr_feed_pull, public.ibkr_borrow,
              public.ibkr_latest, public.ibkr_poll_attempt, public.ibkr_borrow_daily
    from anon, authenticated;
grant select on public.ibkr_symbol, public.ibkr_feed_pull, public.ibkr_borrow,
                public.ibkr_latest, public.ibkr_poll_attempt, public.ibkr_borrow_daily
    to authenticated;

drop policy if exists "authenticated read ibkr_symbol" on public.ibkr_symbol;
create policy "authenticated read ibkr_symbol" on public.ibkr_symbol
    for select to authenticated using (true);
drop policy if exists "authenticated read ibkr_feed_pull" on public.ibkr_feed_pull;
create policy "authenticated read ibkr_feed_pull" on public.ibkr_feed_pull
    for select to authenticated using (true);
drop policy if exists "authenticated read ibkr_borrow" on public.ibkr_borrow;
create policy "authenticated read ibkr_borrow" on public.ibkr_borrow
    for select to authenticated using (true);
drop policy if exists "authenticated read ibkr_latest" on public.ibkr_latest;
create policy "authenticated read ibkr_latest" on public.ibkr_latest
    for select to authenticated using (true);
drop policy if exists "authenticated read ibkr_poll_attempt" on public.ibkr_poll_attempt;
create policy "authenticated read ibkr_poll_attempt" on public.ibkr_poll_attempt
    for select to authenticated using (true);
drop policy if exists "authenticated read ibkr_borrow_daily" on public.ibkr_borrow_daily;
create policy "authenticated read ibkr_borrow_daily" on public.ibkr_borrow_daily
    for select to authenticated using (true);

revoke all on function public.ibkr_borrow_asof(timestamptz) from public, anon;
grant execute on function public.ibkr_borrow_asof(timestamptz) to authenticated;
