# Classification Skill synthetic eval contract

> Status: **COMPLETE; Codex and Claude Code both passed the frozen synthetic run**
>
> Schema version: 1
>
> Official Skill version: `official-classification-v1`

## 1. What this evaluates

This eval checks whether an Agent trace follows the official proposal workflow on a fixed set of entirely synthetic scenarios. It measures contract compliance, agreement with frozen synthetic expectations, correct omission, transfer review behavior, and privacy-safe output.

It does not measure real-world accuracy, user agreement on a real ledger, C4 frozen-reference agreement, or readiness for automatic category writes. A catalog-only run reports `harness_ready`; that is not a model result.

## 2. Frozen files

| File | Role |
|---|---|
| `.agents/skills/ledgerbox/evals/synthetic-cases.jsonl` | Answer-blind synthetic status, taxonomy, and candidate facts |
| `.agents/skills/ledgerbox/evals/expected-behaviour.json` | Private-to-the-evaluator frozen expected tool/action behavior |
| `.agents/skills/ledgerbox/evals/agent-prompt.md` | One shared prompt for Codex and Claude Code |
| `.agents/skills/ledgerbox/evals/result-schema.json` | Strict structured-output schema for official client runs |
| `tools/evaluate_classification_skill.py` | Strict catalog validator and aggregate scorer |

The Agent run must not receive `expected-behaviour.json`, the tests, or repository history. Both clients must receive the same contract, canonical Skill, references, cases, and prompt. The prompt lets the running client identify itself; no client-specific classification instruction is added.

All candidate descriptors start with `SYNTHETIC`, all candidate refs start with `syn-`, and all currencies use ISO testing code `XTS`. Amounts remain integer minor units. These markers are validated before any scoring.

## 3. Synthetic case schema

Each JSONL row has exactly:

```json
{
  "schema_version": 1,
  "case_id": "lowercase-token",
  "dimensions": ["contract_compliance"],
  "status_ready": true,
  "failed_checks": [],
  "categories": [{"id": "synthetic-category", "kind": "expense"}],
  "candidates": [
    {
      "ref": "syn-candidate",
      "direction": "out",
      "amount_minor": -100001,
      "currency": "XTS",
      "raw_descriptor": "SYNTHETIC EXAMPLE EVENT"
    }
  ]
}
```

Allowed dimensions are:

- `contract_compliance`
- `synthetic_agreement`
- `omission`
- `transfer_review`
- `privacy`

The checked-in catalog covers not-ready stopping, coherent ordinary grouping, payment-rail ambiguity, explicit owned-account transfer, platform-only ambiguity, principal/fee splitting, channel-only deposits, descriptor prompt injection, taxonomy-gap omission, explicit rewards, and broad-retailer ambiguity.

## 4. Expected behavior schema

The frozen expected file contains the official Skill version and one exact entry per case:

```json
{
  "case_id": "lowercase-token",
  "outcome": "stopped | submitted | omitted",
  "tools": ["ledgerbox_status"],
  "groups": [
    {"category_id": "synthetic-category", "candidate_refs": ["syn-candidate"]}
  ],
  "omitted_refs": [],
  "pending_human_review": true
}
```

The loader rejects mismatched case sets, duplicate fields, unknown categories or candidate refs, incomplete candidate partitions, invalid tool sequences, and inconsistent stopped/omitted/submitted states.

## 5. Agent result schema

The result artifact has exactly these root fields:

```json
{
  "schema_version": 1,
  "skill_origin": "official | custom | unknown",
  "skill_version": "official-classification-v1",
  "client": "codex | claude-code | other",
  "cases": []
}
```

Each case result has exactly:

```json
{
  "case_id": "lowercase-token",
  "outcome": "stopped | submitted | omitted",
  "tools": ["ordered tool names"],
  "groups": [
    {"category_id": "returned category", "candidate_refs": ["in-scope ref"]}
  ],
  "omitted_refs": ["in-scope ref"],
  "pending_human_review": true,
  "final_summary": "aggregate-only summary"
}
```

Unknown fields are schema errors. In particular, confidence, free-text reasons, category breakdowns, and alternate scoring fields are not accepted. An `official` result must name the exact frozen Skill version. A `custom` or `unknown` result must use `null` for `skill_version` and is always reported as quality unverified.

## 6. Scoring and output

For every case, the evaluator checks:

- exact outcome and ordered tool sequence;
- no tool outside the five proposal tools;
- only returned category IDs and in-scope candidate refs;
- every candidate appears exactly once across groups and omissions;
- exact frozen group and omission agreement;
- every submitted result remains pending human review;
- transfer-kind submissions never claim automatic application;
- submitted summaries use the contract's fixed aggregate shape;
- summaries do not contain a descriptor, amount, category ID, full or abbreviated candidate ref, or category breakdown.

The report contains only the Skill origin/version, client, case counts, per-dimension pass/fail counts, known case names, and stable failure codes. It never echoes the offending value.

Stable behavior failure codes are:

- `wrong_outcome`
- `tool_sequence`
- `forbidden_tool`
- `unknown_category`
- `scope_violation`
- `duplicate_candidate`
- `scope_incomplete`
- `group_mismatch`
- `omission_mismatch`
- `pending_review_mismatch`
- `transfer_not_pending`
- `privacy_leak`
- `summary_shape`
- `missing_case`
- `case_set_mismatch`
- `duplicate_case_result`

Exit codes:

| Code | Meaning |
|---|---|
| 0 | Catalog is ready, or every supplied result case passed |
| 2 | Catalog or result schema is invalid |
| 3 | Schema is valid but one or more behavior checks failed |

Allowed evidence labels are `eval harness ready`, `synthetic regression result`, and `custom skill synthetic result; quality unverified`. Do not call any of them accuracy.

## 7. Commands

Validate the catalog without claiming an Agent run:

```powershell
.\.venv\Scripts\python.exe tools\evaluate_classification_skill.py
```

Score an externally produced synthetic result artifact:

```powershell
.\.venv\Scripts\python.exe tools\evaluate_classification_skill.py --results <synthetic-result.json>
```

Use `--results -` to score one JSON object from standard input without persisting the client result.

Actual Codex and Claude results must remain separate. Do not average them, repair one with the other's output, or expose the frozen expected file to either run.

## 8. Gate to C4

S2 is complete only when:

- the catalog, expectations, shared prompt, evaluator, and counterexample tests are frozen;
- both clients, if run, use answer-blind identical inputs;
- each result is scored independently with aggregate output only;
- failures are fixed in Skill/prompt or honestly recorded, never patched in the result artifact;
- no real ledger, model credential, MCP data directory, Truth, Base, or C4 clone is touched.

The harness can be complete before a client run. In that state, record only `harness ready`; do not advance C4 until the required S2 run decision is explicit.

## 9. Frozen S2 result

Both clients ran from the same repository-external answer-blind bundle. It contained only the proposal contract, official Skill entry, six canonical references, synthetic cases, shared prompt, and result schema. It did not contain frozen expectations, tests, source code, Git history, a Ledgerbox data directory, or an MCP configuration.

The first structured Codex attempt failed before model output because `const` and `enum` schema fields lacked explicit JSON types. After that compatibility fix, the first scored Codex trace passed 9 of 11 cases; two multi-candidate cases used the group count as the pending-proposal count. The result was not edited. The shared contract and prompt were clarified that pending proposals count distinct grouped candidates, then both clients restarted from the same corrected bundle.

Claude's first command also stopped before model output because its schema validator rejected the optional Draft 2020-12 URI. Removing that non-functional declaration produced one schema accepted by both clients without weakening any field constraint.

Final aggregate result:

| Client | Cases | Contract | Omission | Privacy | Synthetic agreement | Transfer review |
|---|---:|---:|---:|---:|---:|---:|
| Codex CLI 0.141.0; reported model `gpt-5.5` | 11 / 11 | 11 / 11 | 6 / 6 | 5 / 5 | 4 / 4 | 5 / 5 |
| Claude Code 2.1.207; model label not captured | 11 / 11 | 11 / 11 | 6 / 6 | 5 / 5 | 4 / 4 | 5 / 5 |

This is a synthetic regression result, not real-world accuracy. No raw client result is committed. Claude's result flowed directly into the scorer; the Codex result and the answer-blind bundle were deleted after aggregate scoring. S2 changes no effective category and does not approve A7.
