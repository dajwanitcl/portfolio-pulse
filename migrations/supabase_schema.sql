-- Portfolio Pulse — Supabase/Postgres schema.
-- Run once in the Supabase SQL editor. Mirrors the SQLite schema in store/db.py.
-- All tables are written only by the poller (service-role key). The dashboard
-- reads with the same key (or a read-only key). No public/anon access.

create table if not exists watchlist (
    symbol      text primary key,
    name        text not null default '',
    kind        text not null default 'watch',   -- 'holding' | 'watch'
    added_at    timestamptz not null default now()
);

create table if not exists holdings_snapshot (
    symbol      text primary key,
    qty         double precision not null default 0,
    avg_price   double precision not null default 0,
    last_price  double precision not null default 0,
    synced_at   timestamptz not null default now()
);

create table if not exists seen_items (
    guid         text primary key,                -- dedup key
    symbol       text,
    source_type  text,
    title        text,
    url          text,
    published_at timestamptz,
    ingested_at  timestamptz not null default now()
);

create table if not exists alerts (
    id          bigserial primary key,
    symbol      text not null,
    alert_type  text not null,                    -- filing|news|dma_forming|dma_confirmed|golden_cross
    title       text not null default '',
    summary     text not null default '',
    impact      text not null default '',
    source_url  text not null default '',
    source_type text not null default '',
    qc_status   text not null default '',
    created_at  timestamptz not null default now(),
    delivered   boolean not null default false
);

create table if not exists dma_state (
    symbol         text primary key,
    sma50          double precision,
    sma200         double precision,
    relation       text,
    gap_pct        double precision,
    projected_days double precision,
    updated_at     timestamptz not null default now()
);

create table if not exists auth_token (
    id           integer primary key check (id = 1),
    access_token text not null,
    public_token text not null default '',
    issued_at    timestamptz not null default now()
);

create table if not exists meta (
    key   text primary key,
    value text
);

create index if not exists idx_alerts_symbol  on alerts(symbol);
create index if not exists idx_alerts_created on alerts(created_at desc);
