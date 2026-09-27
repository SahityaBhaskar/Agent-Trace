-- =============================================================================
-- Migration 002: Add arch_graph column to agent_sessions
-- Apply via: Supabase SQL Editor or supabase db push
-- =============================================================================

alter table agent_sessions add column if not exists arch_graph jsonb;

create index if not exists idx_agent_sessions_arch_graph_gin
    on agent_sessions using gin (arch_graph);

notify pgrst, 'reload schema';
