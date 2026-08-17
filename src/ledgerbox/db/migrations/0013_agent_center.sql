-- A7.3 stores explicit local Agent consent separately from financial answers.
-- Session evidence is aggregate-only: no transaction, proposal or revision id.

CREATE TABLE agent_local_policy (
  id                         INTEGER PRIMARY KEY CHECK (id = 1),
  selected_client            TEXT CHECK (selected_client IN ('codex', 'claude-code')),
  application_mode           TEXT NOT NULL DEFAULT 'automatic'
                                   CHECK (application_mode IN ('review_first', 'automatic')),
  enabled                    INTEGER NOT NULL DEFAULT 0 CHECK (enabled IN (0, 1)),
  auto_classify_new_imports  INTEGER NOT NULL DEFAULT 1 CHECK (auto_classify_new_imports IN (0, 1)),
  updated_at                 TEXT NOT NULL,
  CHECK (enabled = 0 OR selected_client IS NOT NULL)
) STRICT;

INSERT INTO agent_local_policy
  (id, selected_client, application_mode, enabled, auto_classify_new_imports, updated_at)
VALUES
  (1, NULL, 'automatic', 0, 1, '1970-01-01T00:00:00+00:00');

CREATE TABLE agent_local_session (
  id               TEXT PRIMARY KEY,
  client           TEXT NOT NULL CHECK (client IN ('codex', 'claude-code')),
  started_at       TEXT NOT NULL,
  last_seen_at     TEXT NOT NULL,
  ended_at         TEXT,
  result_state     TEXT NOT NULL DEFAULT 'none'
                        CHECK (result_state IN ('none', 'completed', 'partial', 'failed')),
  result_at        TEXT,
  candidate_count  INTEGER CHECK (candidate_count IS NULL OR candidate_count >= 0),
  submitted_count  INTEGER CHECK (submitted_count IS NULL OR submitted_count >= 0),
  error_code       TEXT,
  CHECK (
    (result_state = 'none'
      AND result_at IS NULL
      AND candidate_count IS NULL
      AND submitted_count IS NULL
      AND error_code IS NULL)
    OR
    (result_state = 'completed'
      AND result_at IS NOT NULL
      AND candidate_count IS NOT NULL
      AND submitted_count = candidate_count
      AND error_code IS NULL)
    OR
    (result_state = 'partial'
      AND result_at IS NOT NULL
      AND candidate_count IS NOT NULL
      AND submitted_count IS NOT NULL
      AND submitted_count < candidate_count
      AND error_code IS NULL)
    OR
    (result_state = 'failed'
      AND result_at IS NOT NULL
      AND candidate_count IS NULL
      AND submitted_count IS NULL
      AND error_code IS NOT NULL)
  )
) STRICT;

CREATE INDEX agent_local_session_client_seen
  ON agent_local_session(client, last_seen_at DESC);

CREATE INDEX agent_local_session_client_result
  ON agent_local_session(client, result_at DESC)
  WHERE result_state <> 'none';
