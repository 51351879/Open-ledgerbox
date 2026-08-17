-- A7.4 binds one queued job to the exact MCP session and proposal run that
-- executed it. Nullable fields preserve standalone/manual MCP sessions and
-- jobs that fail before a client starts or before a proposal is submitted.

ALTER TABLE agent_classification_job
  ADD COLUMN session_id TEXT REFERENCES agent_local_session(id) ON DELETE SET NULL;

ALTER TABLE agent_classification_job
  ADD COLUMN proposal_run_id TEXT REFERENCES agent_proposal_run(id) ON DELETE SET NULL;

CREATE UNIQUE INDEX agent_classification_job_session
  ON agent_classification_job(session_id) WHERE session_id IS NOT NULL;

CREATE UNIQUE INDEX agent_classification_job_proposal_run
  ON agent_classification_job(proposal_run_id) WHERE proposal_run_id IS NOT NULL;
