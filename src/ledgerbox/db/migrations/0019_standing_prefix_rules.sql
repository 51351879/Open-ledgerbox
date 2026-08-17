-- A7.9: a person may decree a standing rule at the descriptor-prefix grain.
--
-- The template grain keeps every letter, so "ZELLE PAYMENT TO" one payee and
-- the same words to another payee are different templates -- deliberately, an
-- agent must never treat them as one. But the OWNER of the ledger may know a
-- broader fact about their own money: every outgoing Zelle of theirs is a
-- transfer between their own accounts. That is a human decree, not an
-- inference, so prefix rules are human-only by CHECK, need no teaching
-- transaction, and carry a minimum length so a two-character prefix cannot
-- quietly claim the whole ledger.
--
-- learned_rule cannot be widened in place, and it is category_override's
-- foreign-key parent, so with foreign keys enforced the child must step aside
-- first. Both tables hop through scratch copies, and -- as in 0018 -- nothing
-- here uses ALTER TABLE RENAME, because a rename re-parses every view over the
-- schema mid-flight. The final CREATE bears the final name.

DROP VIEW v_txn_category;

DROP VIEW v_txn_transfer;

CREATE TABLE category_override_migration_scratch (
  txn_id          TEXT PRIMARY KEY,
  category_id     TEXT NOT NULL,
  created_at      TEXT NOT NULL,
  source          TEXT NOT NULL,
  agent_run_id    TEXT,
  learned_rule_id TEXT
) STRICT;

INSERT INTO category_override_migration_scratch
  (txn_id, category_id, created_at, source, agent_run_id, learned_rule_id)
SELECT txn_id, category_id, created_at, source, agent_run_id, learned_rule_id
FROM category_override;

DROP TABLE category_override;

CREATE TABLE learned_rule_migration_scratch (
  id                  TEXT PRIMARY KEY,
  template            TEXT NOT NULL,
  template_version    INTEGER NOT NULL,
  category_id         TEXT NOT NULL,
  source              TEXT NOT NULL,
  agent_run_id        TEXT,
  learned_from_txn_id TEXT,
  created_at          TEXT NOT NULL
) STRICT;

INSERT INTO learned_rule_migration_scratch
  (id, template, template_version, category_id, source, agent_run_id,
   learned_from_txn_id, created_at)
SELECT id, template, template_version, category_id, source, agent_run_id,
       learned_from_txn_id, created_at
FROM learned_rule;

DROP TABLE learned_rule;

CREATE TABLE learned_rule (
  id                  TEXT PRIMARY KEY
                           CHECK (length(id) = 35 AND substr(id, 1, 3) = 'lr-'),
  match_kind          TEXT NOT NULL DEFAULT 'template'
                           CHECK (match_kind IN ('template', 'prefix')),
  template            TEXT NOT NULL CHECK (length(template) > 0),
  template_version    INTEGER NOT NULL CHECK (template_version >= 1),
  category_id         TEXT NOT NULL REFERENCES category(id),
  source              TEXT NOT NULL CHECK (source IN ('human', 'agent')),
  agent_run_id        TEXT REFERENCES agent_proposal_run(id) ON DELETE CASCADE,
  learned_from_txn_id TEXT REFERENCES txn(id),
  created_at          TEXT NOT NULL,
  CHECK (
    (source = 'human' AND agent_run_id IS NULL)
    OR
    (source = 'agent' AND agent_run_id IS NOT NULL)
  ),
  -- A template rule is always learned from one concrete decision. A prefix
  -- rule is a standing human decree with no single teaching transaction.
  CHECK (match_kind = 'prefix' OR learned_from_txn_id IS NOT NULL),
  CHECK (
    match_kind = 'template'
    OR (source = 'human' AND learned_from_txn_id IS NULL AND length(template) >= 6)
  ),
  UNIQUE (match_kind, template, template_version)
) STRICT;

INSERT INTO learned_rule
  (id, match_kind, template, template_version, category_id, source, agent_run_id,
   learned_from_txn_id, created_at)
SELECT id, 'template', template, template_version, category_id, source, agent_run_id,
       learned_from_txn_id, created_at
FROM learned_rule_migration_scratch;

DROP TABLE learned_rule_migration_scratch;

CREATE INDEX learned_rule_run
  ON learned_rule(agent_run_id) WHERE agent_run_id IS NOT NULL;

CREATE TABLE category_override (
  txn_id          TEXT PRIMARY KEY REFERENCES txn(id),
  category_id     TEXT NOT NULL REFERENCES category(id),
  created_at      TEXT NOT NULL,
  source          TEXT NOT NULL DEFAULT 'human'
                       CHECK (source IN ('human', 'agent', 'learned')),
  agent_run_id    TEXT REFERENCES "agent_proposal_run"(id),
  learned_rule_id TEXT REFERENCES learned_rule(id),
  CHECK (
    (source = 'human' AND agent_run_id IS NULL AND learned_rule_id IS NULL)
    OR
    (source = 'agent' AND agent_run_id IS NOT NULL AND learned_rule_id IS NULL)
    OR
    (source = 'learned' AND agent_run_id IS NULL AND learned_rule_id IS NOT NULL)
  )
) STRICT;

INSERT INTO category_override
  (txn_id, category_id, created_at, source, agent_run_id, learned_rule_id)
SELECT txn_id, category_id, created_at, source, agent_run_id, learned_rule_id
FROM category_override_migration_scratch;

DROP TABLE category_override_migration_scratch;

CREATE VIEW v_txn_category AS
SELECT
  t.id AS txn_id,
  COALESCE(
    co.category_id,
    (SELECT p.category_id
       FROM posting p
      WHERE p.txn_id = t.id AND p.category_id IS NOT NULL
      ORDER BY p.seq
      LIMIT 1)
  ) AS category_id,
  CASE
    WHEN co.category_id IS NOT NULL AND co.source = 'agent' THEN 'agent'
    WHEN co.category_id IS NOT NULL AND co.source = 'learned' THEN 'learned'
    WHEN co.category_id IS NOT NULL THEN 'override'
    WHEN EXISTS (
      SELECT 1 FROM posting p WHERE p.txn_id = t.id AND p.category_id IS NOT NULL
    ) THEN 'rule'
    ELSE 'none'
  END AS decided_by
FROM txn t
LEFT JOIN category_override co ON co.txn_id = t.id;

CREATE VIEW v_txn_transfer AS
SELECT
  t.id AS txn_id,
  CASE
    WHEN co.category_id IS NULL  THEN t.is_transfer
    WHEN c.kind = 'transfer'     THEN 1
    ELSE 0
  END AS is_transfer,
  CASE
    WHEN co.category_id IS NULL THEN 'rule'
    WHEN co.source = 'agent' THEN 'agent'
    WHEN co.source = 'learned' THEN 'learned'
    ELSE 'override'
  END AS decided_by
FROM txn t
LEFT JOIN category_override co ON co.txn_id = t.id
LEFT JOIN category c ON c.id = co.category_id;
