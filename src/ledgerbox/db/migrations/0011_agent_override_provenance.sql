-- Honest source attribution for effective category overrides.
--
-- Existing rows were created only by explicit human review or the transaction
-- editor, so the forward-migration default is human.  A future automatic
-- proposal path must name its originating audit run; Core rejects an Agent
-- source without that durable link.

ALTER TABLE category_override
  ADD COLUMN source TEXT NOT NULL DEFAULT 'human'
    CHECK (source IN ('human', 'agent'));

ALTER TABLE category_override
  ADD COLUMN agent_run_id TEXT REFERENCES agent_proposal_run(id)
    CHECK (
      (source = 'human' AND agent_run_id IS NULL)
      OR
      (source = 'agent' AND agent_run_id IS NOT NULL)
    );

-- Both effective-answer views report the same provenance vocabulary.  The
-- public API retains `override` for a human answer so proposal-v1 clients do
-- not change meaning; only a new Agent-owned answer introduces `agent`.
DROP VIEW v_txn_category;

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
    WHEN co.category_id IS NOT NULL THEN 'override'
    WHEN EXISTS (
      SELECT 1 FROM posting p WHERE p.txn_id = t.id AND p.category_id IS NOT NULL
    ) THEN 'rule'
    ELSE 'none'
  END AS decided_by
FROM txn t
LEFT JOIN category_override co ON co.txn_id = t.id;

DROP VIEW v_txn_transfer;

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
    ELSE 'override'
  END AS decided_by
FROM txn t
LEFT JOIN category_override co ON co.txn_id = t.id
LEFT JOIN category c ON c.id = co.category_id;
