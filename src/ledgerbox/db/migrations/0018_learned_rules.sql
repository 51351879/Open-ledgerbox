-- A7.7: a person's answer, or an accepted Agent answer, becomes an asset.
--
-- Until now `category_override` was keyed on one txn_id: every human decision
-- covered exactly one transaction, forever. The same merchant next month was
-- nobody's business again, so coverage never compounded and the residual pool
-- was re-litigated from scratch by every classification round. This is the
-- learning loop every mature transaction classifier is built on, and the one
-- structural piece this ledger was missing.
--
-- `learned_rule` is keyed on the descriptor template (per-visit digit runs
-- masked; see ledgerbox.descriptor_template) and carries the same provenance
-- discipline as everything else here: a human rule names the decision it was
-- learned from, an agent rule names its originating run so a whole-run
-- withdrawal can take its rules and their downstream effects with it. An
-- override written by a rule says source='learned' and names the rule -- it is
-- never dressed up as a fresh human decision, because it is not one.

CREATE TABLE learned_rule (
  id                  TEXT PRIMARY KEY
                           CHECK (length(id) = 35 AND substr(id, 1, 3) = 'lr-'),
  template            TEXT NOT NULL CHECK (length(template) > 0),
  template_version    INTEGER NOT NULL CHECK (template_version >= 1),
  category_id         TEXT NOT NULL REFERENCES category(id),
  source              TEXT NOT NULL CHECK (source IN ('human', 'agent')),
  agent_run_id        TEXT REFERENCES agent_proposal_run(id) ON DELETE CASCADE,
  learned_from_txn_id TEXT NOT NULL REFERENCES txn(id),
  created_at          TEXT NOT NULL,
  CHECK (
    (source = 'human' AND agent_run_id IS NULL)
    OR
    (source = 'agent' AND agent_run_id IS NOT NULL)
  ),
  UNIQUE (template, template_version)
) STRICT;

CREATE INDEX learned_rule_run
  ON learned_rule(agent_run_id) WHERE agent_run_id IS NOT NULL;

-- SQLite cannot widen a CHECK in place, so the override table is rebuilt. The
-- rebuild deliberately never uses ALTER TABLE RENAME: a rename re-parses every
-- view in the schema, and the whole tower above these two -- v_transaction,
-- v_category_spend, v_cashflow_line -- would abort it while the base views are
-- mid-replacement. Rows hop through a scratch table instead, and the final
-- CREATE bears the final name.

DROP VIEW v_txn_category;

DROP VIEW v_txn_transfer;

CREATE TABLE category_override_migration_scratch (
  txn_id       TEXT PRIMARY KEY,
  category_id  TEXT NOT NULL,
  created_at   TEXT NOT NULL,
  source       TEXT NOT NULL,
  agent_run_id TEXT
) STRICT;

INSERT INTO category_override_migration_scratch
  (txn_id, category_id, created_at, source, agent_run_id)
SELECT txn_id, category_id, created_at, source, agent_run_id
FROM category_override;

DROP TABLE category_override;

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
  (txn_id, category_id, created_at, source, agent_run_id)
SELECT txn_id, category_id, created_at, source, agent_run_id
FROM category_override_migration_scratch;

DROP TABLE category_override_migration_scratch;

-- Both views fold overrides into one answer and must say who decided. Absorbing
-- 'learned' into the human bucket would put a machine-applied answer behind a
-- person's name, which is the exact dishonesty this schema exists to prevent.

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
