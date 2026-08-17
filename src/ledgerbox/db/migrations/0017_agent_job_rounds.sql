-- A7.6 lets a classification round be asked for, and lets one round follow
-- another while it is still finding work.
--
-- Until now the only way to queue a run was a successful import, and the trigger
-- was a NOT NULL UNIQUE reference to one imported file. An operator looking at
-- transactions the Agent had left alone could not ask for another pass at all;
-- their only lever was importing another statement. A real run classified 152 of
-- 270 candidates across thirteen accidental rounds -- one per uploaded file --
-- and then stopped because the files ran out rather than because the work did.
--
-- `trigger_kind` names why a job exists and `round_index` bounds how far a chain
-- of rounds may go. SQLite cannot relax NOT NULL in place, so the table is
-- rebuilt; no other table references it.

CREATE TABLE agent_classification_job_rounds (
  id                     TEXT PRIMARY KEY
                               CHECK (length(id) = 36 AND substr(id, 1, 4) = 'job-'),
  trigger_source_file_id TEXT REFERENCES source_file(id) ON DELETE CASCADE,
  trigger_kind           TEXT NOT NULL DEFAULT 'import'
                               CHECK (trigger_kind IN ('import','manual','followup')),
  round_index            INTEGER NOT NULL DEFAULT 1
                               CHECK (round_index BETWEEN 1 AND 99),
  client                 TEXT NOT NULL CHECK (client IN ('codex','claude-code')),
  application_mode       TEXT NOT NULL CHECK (application_mode IN ('review_first','automatic')),
  state                  TEXT NOT NULL DEFAULT 'queued'
                               CHECK (state IN ('queued','running','completed','partial','failed')),
  candidate_count        INTEGER CHECK (candidate_count IS NULL OR candidate_count >= 0),
  submitted_count        INTEGER CHECK (submitted_count IS NULL OR submitted_count >= 0),
  applied_count          INTEGER CHECK (applied_count IS NULL OR applied_count >= 0),
  omitted_count          INTEGER CHECK (omitted_count IS NULL OR omitted_count >= 0),
  error_code             TEXT CHECK (
                               error_code IS NULL
                               OR (length(error_code) BETWEEN 1 AND 64
                                   AND error_code NOT GLOB '*[^a-z0-9_]*')
                             ),
  client_outcome         TEXT CHECK (
                               client_outcome IS NULL
                               OR client_outcome IN
                                  ('exited','timeout','not_found','spawn_failed','workspace_missing')
                             ),
  client_exit_code       INTEGER,
  client_log_excerpt     TEXT,
  queued_at              TEXT NOT NULL,
  started_at             TEXT,
  finished_at            TEXT,
  session_id             TEXT REFERENCES agent_local_session(id) ON DELETE SET NULL,
  proposal_run_id        TEXT REFERENCES agent_proposal_run(id) ON DELETE SET NULL,
  -- An import job is the one kind that names a file, and it is the only kind
  -- that may: a job asked for by a person is not about any one statement.
  CHECK ((trigger_kind = 'import') = (trigger_source_file_id IS NOT NULL)),
  CHECK (
    (state = 'queued'
      AND started_at IS NULL AND finished_at IS NULL
      AND candidate_count IS NULL AND submitted_count IS NULL
      AND applied_count IS NULL AND omitted_count IS NULL AND error_code IS NULL)
    OR
    (state = 'running'
      AND started_at IS NOT NULL AND finished_at IS NULL
      AND candidate_count IS NULL AND submitted_count IS NULL
      AND applied_count IS NULL AND omitted_count IS NULL AND error_code IS NULL)
    OR
    (state IN ('completed','partial')
      AND started_at IS NOT NULL AND finished_at IS NOT NULL
      AND candidate_count IS NOT NULL AND submitted_count IS NOT NULL
      AND applied_count IS NOT NULL AND omitted_count IS NOT NULL
      AND submitted_count + omitted_count = candidate_count
      AND applied_count <= submitted_count
      AND error_code IS NULL
      AND ((state = 'completed' AND omitted_count = 0)
           OR (state = 'partial' AND omitted_count > 0)))
    OR
    (state = 'failed'
      AND started_at IS NOT NULL AND finished_at IS NOT NULL
      AND candidate_count IS NOT NULL AND submitted_count = 0
      AND applied_count = 0 AND omitted_count = candidate_count
      AND error_code IS NOT NULL)
  )
) STRICT;

INSERT INTO agent_classification_job_rounds
  (id, trigger_source_file_id, trigger_kind, round_index, client, application_mode,
   state, candidate_count, submitted_count, applied_count, omitted_count, error_code,
   client_outcome, client_exit_code, client_log_excerpt,
   queued_at, started_at, finished_at, session_id, proposal_run_id)
SELECT
   id, trigger_source_file_id, 'import', 1, client, application_mode,
   state, candidate_count, submitted_count, applied_count, omitted_count, error_code,
   client_outcome, client_exit_code, client_log_excerpt,
   queued_at, started_at, finished_at, session_id, proposal_run_id
FROM agent_classification_job;

DROP TABLE agent_classification_job;

ALTER TABLE agent_classification_job_rounds RENAME TO agent_classification_job;

CREATE UNIQUE INDEX agent_classification_job_import_trigger
  ON agent_classification_job(trigger_source_file_id)
  WHERE trigger_source_file_id IS NOT NULL;

CREATE UNIQUE INDEX agent_classification_job_one_running
  ON agent_classification_job(state) WHERE state = 'running';

CREATE INDEX agent_classification_job_queue
  ON agent_classification_job(state, queued_at, id);

CREATE UNIQUE INDEX agent_classification_job_session
  ON agent_classification_job(session_id) WHERE session_id IS NOT NULL;

CREATE UNIQUE INDEX agent_classification_job_proposal_run
  ON agent_classification_job(proposal_run_id) WHERE proposal_run_id IS NOT NULL;
