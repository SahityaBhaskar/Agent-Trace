-- =============================================================================
-- AgentTrace — Supabase Schema Migration
-- Apply via: Supabase SQL Editor or supabase db push
-- =============================================================================

-- ── Extensions ────────────────────────────────────────────────────────────────
create extension if not exists "pgcrypto";

-- ── Table: agent_sessions ─────────────────────────────────────────────────────
-- One row per analyzed repository scenario (maps 1:1 to ScenarioData).
create table if not exists agent_sessions (
    id               text        primary key default gen_random_uuid()::text,
    repo_path        text        not null,
    repo_name        text        not null default '',
    title            text        not null default '',
    description      text        not null default '',
    user_prompt      text        not null default '',
    diff_target      text        not null default 'auto',
    agent_id         text        not null default 'unknown',
    transcript_path  text,
    payload          jsonb       not null default '{}',
    stats            jsonb       not null default '{}',
    arch_graph       jsonb,
    created_at       timestamptz not null default now()
);

-- Indexes for common list/filter queries
create index if not exists idx_agent_sessions_repo_path
    on agent_sessions (repo_path);

create index if not exists idx_agent_sessions_created_at
    on agent_sessions (created_at desc);

create index if not exists idx_agent_sessions_agent_id
    on agent_sessions (agent_id);

-- GIN index for JSONB payload queries (optional but useful for debugging)
create index if not exists idx_agent_sessions_payload_gin
    on agent_sessions using gin (payload);

create index if not exists idx_agent_sessions_arch_graph_gin
    on agent_sessions using gin (arch_graph);

-- ── Table: session_events ─────────────────────────────────────────────────────
-- Individual SessionEvent rows — the agent-action timeline.
create table if not exists session_events (
    id               text        primary key default gen_random_uuid()::text,
    session_id       text        not null references agent_sessions (id) on delete cascade,
    sequence_number  integer     not null default 0,
    timestamp        text,
    agent_id         text        not null default 'unknown',
    action_type      text        not null,
    epistemic_status text        not null default 'OBSERVED',
    target           jsonb,
    payload          jsonb,
    parent_event_id  text
);

create index if not exists idx_session_events_session_id
    on session_events (session_id, sequence_number);

-- ── Table: discovered_sessions_cache ─────────────────────────────────────────
-- Cache for auto-discovered AI agent sessions (Antigravity, Claude Code, etc.)
create table if not exists discovered_sessions_cache (
    id               text        primary key default gen_random_uuid()::text,
    host_machine     text        not null,
    repo_path        text        not null default '',
    provider_id      text        not null default '',
    transcript_path  text        not null,
    agent_name       text        not null default '',
    last_modified    text,
    session_data     jsonb       not null default '{}',
    scanned_at       timestamptz not null default now(),

    -- one cache entry per (host, transcript) pair
    unique (host_machine, transcript_path)
);

create index if not exists idx_discovered_sessions_host
    on discovered_sessions_cache (host_machine, scanned_at desc);

create index if not exists idx_discovered_sessions_repo
    on discovered_sessions_cache (repo_path);

-- ── Row Level Security ────────────────────────────────────────────────────────
-- RLS is DISABLED for all tables since AgentTrace runs as a local single-user
-- backend server using the anon key with full trust.
-- Enable and add policies if you add multi-user auth in the future.

alter table agent_sessions           disable row level security;
alter table session_events           disable row level security;
alter table discovered_sessions_cache disable row level security;

-- ── Done ──────────────────────────────────────────────────────────────────────
-- Verify with:
--   select table_name from information_schema.tables
--   where table_schema = 'public';
