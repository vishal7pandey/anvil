CREATE DATABASE litellm OWNER anvil;
\c litellm
CREATE TABLE IF NOT EXISTS "LiteLLM_SpendLogs" (
    metadata JSONB,
    spend NUMERIC
);
CREATE INDEX IF NOT EXISTS ix_litellm_spend_logs_run_id
ON "LiteLLM_SpendLogs" (((metadata -> 'spend_logs_metadata') ->> 'run_id'));
\c anvil
CREATE EXTENSION IF NOT EXISTS vector;
