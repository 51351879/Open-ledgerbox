-- A7.4 persists one bounded auto-classification trigger per successful import.
-- Only aggregate counts cross this boundary; candidate and proposal ids remain
-- in their existing narrow audit tables.

CREATE TABLE agent_classification_job (
  id                     TEXT PRIMARY KEY
                               CHECK (length(id) = 36 AND substr(id, 1, 4) = 'job-'),
  trigger_source_file_id TEXT NOT NULL REFERENCES source_file(id) ON DELETE CASCADE,
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
  queued_at              TEXT NOT NULL,
  started_at             TEXT,
  finished_at            TEXT,
  UNIQUE (trigger_source_file_id),
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

CREATE UNIQUE INDEX agent_classification_job_one_running
  ON agent_classification_job(state) WHERE state = 'running';

CREATE INDEX agent_classification_job_queue
  ON agent_classification_job(state, queued_at, id);

