-- A7.6 keeps the evidence a finished job used to throw away.
--
-- Before this migration the runner sent the client's stdout and stderr to
-- DEVNULL, so a run that was killed at the timeout and a run that deliberately
-- abstained on every candidate produced byte-identical rows. An operator asking
-- "why did it only classify two of these" had nothing to read, and neither did
-- anyone helping them.
--
-- `client_outcome` and `client_exit_code` are aggregate facts and may be shown
-- anywhere. `client_log_excerpt` is the client's own reasoning about real bank
-- descriptors: it is bounded, it stays inside the operator's data directory,
-- and it must never enter an HTTP response. `ledgerbox agent job-log` prints it
-- to the operator's own terminal, which is the only way it is meant to be read.

ALTER TABLE agent_classification_job
  ADD COLUMN client_outcome TEXT CHECK (
    client_outcome IS NULL
    OR client_outcome IN ('exited', 'timeout', 'not_found', 'spawn_failed', 'workspace_missing')
  );

ALTER TABLE agent_classification_job
  ADD COLUMN client_exit_code INTEGER;

ALTER TABLE agent_classification_job
  ADD COLUMN client_log_excerpt TEXT;
