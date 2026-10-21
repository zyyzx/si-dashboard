-- FINRA short interest on plain Postgres (alpha-pg). Run as a superuser / the
-- database owner, connected to the `alpha` database. Idempotent.
--
-- Mirrors supabase/migrations/0001 in schema `finra`, without Supabase's
-- anon / authenticated roles and RLS. Access is plain GRANTs:
--   finra_ingest  -- the CI ingest job (secret DATABASE_URL): read + write
--   finra_reader  -- the dashboard build (secret DATABASE_URL_RO): read only
-- Create the logins once, with long random passwords, outside this file:
--   create role finra_ingest login password '...';
--   create role finra_reader login password '...';

create schema if not exists finra;

create table if not exists finra.finra_short_interest (
    settlement_date   date    not null,
    symbol            text    not null,
    issue_name        text,
    exchange_code     text,
    market_class      text,
    current_short     bigint,
    previous_short    bigint,
    stock_split_flag  text,
    avg_daily_volume  bigint,
    days_to_cover     numeric,
    revision_flag     text,
    change_pct        numeric,
    change_prev       bigint,
    ingested_at       timestamptz not null default now(),
    primary key (settlement_date, symbol)
);
create index if not exists finra_si_symbol_date_idx
    on finra.finra_short_interest (symbol, settlement_date);

create table if not exists finra.finra_ingest_runs (
    settlement_date date primary key,
    row_count       integer not null,
    ingested_at     timestamptz not null default now()
);

do $$
begin
    if exists (select 1 from pg_roles where rolname = 'finra_ingest') then
        grant usage on schema finra to finra_ingest;
        -- delete: a period is replaced in one transaction on reload (ingest_finra_supabase.load_period)
        grant select, insert, update, delete on finra.finra_short_interest, finra.finra_ingest_runs to finra_ingest;
        grant temporary on database alpha to finra_ingest;   -- load_period stages into a temp table
    end if;
    if exists (select 1 from pg_roles where rolname = 'finra_reader') then
        grant usage on schema finra to finra_reader;
        grant select on finra.finra_short_interest, finra.finra_ingest_runs to finra_reader;
    end if;
end $$;
