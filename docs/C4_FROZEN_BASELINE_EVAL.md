# C4 frozen baseline preflight and scorer

> Status: **C4.0-C4.2 COMPLETE; C4.3-C4.4 results recorded separately**
>
> Frozen Skill: `official-classification-v1`
>
> Result language: frozen-reference agreement, never objective accuracy

## 1. Completed preflight

The C4 Base was ingested into a new repository-external data directory from the same 13 archived
statements as Truth. It was not made by copying Truth and deleting overrides or audit rows. The
Codex and Claude clones were then independently created from the verified clean Base.

The aggregate-only preflight passed:

- Truth: schema 10, verifier 9/9, 24 categories, zero effective unclassified rows;
- Base and both clones: schema 10, verifier 9/9, no category override and no Agent audit;
- Truth and Base taxonomy and 13 stable table row counts are equal;
- Base and both clones taxonomy, stable row counts and all-dates candidate sets are equal;
- the candidate-set equality result is `true`, with a shared denominator of 270;
- every Base candidate has one current effective category in Truth;
- the same checkout, rules source and official Skill version are frozen for both clients.

No candidate identifier, revision identifier, descriptor, name, account detail or amount is stored in
this document. The exact directory roles and lifecycle are held in a repository-external local run
record that is explicitly forbidden from being copied into Git.

## 2. Directory roles and lifecycle

| Role | Creation | Permitted mutation | Lifecycle |
|---|---|---|---|
| Truth | Existing completed human-review ledger | None | Preserve read-only through C5 |
| Base | Fresh ingest from Truth's archive | None after 9/9 verification | Preserve as the reproducible denominator |
| Codex clone | Independent copy of clean Base | One proposal-only audit | Retain through local scoring and C5 |
| Claude clone | Independent copy of clean Base | One proposal-only audit | Retain through local scoring and C5 |

The default product data directory is outside this scope. Clone cleanup or long-term retention is a
C5 handoff action and must name these exact C4 roles; no broad recursive cleanup is authorized.

## 3. Equality proof

`tools/evaluate_frozen_baseline.py preflight` opens all four databases read-only. Candidate identifiers
exist only in process memory as sets. It compares the sets directly and serializes only:

- equality boolean;
- shared candidate count;
- taxonomy count and equality;
- stable-table count and row-count equality;
- verifier counts, schema version, clean-clone state and Truth-reference completeness.

The error path uses stable aggregate codes and never interpolates a candidate identifier or local data
path. A mismatch exits non-zero before either model is run.

## 4. Frozen scoring definitions

Each client is scored independently against the same Base denominator and Truth reference:

| Metric | Frozen numerator / denominator |
|---|---|
| Candidate denominator | All all-dates Base candidates |
| Proposal coverage | Distinct proposed candidates / candidate denominator |
| Frozen-reference agreement | Exact category matches / proposed candidates |
| Ordinary agreement | Exact non-transfer proposals / non-transfer proposals |
| Transfer agreement | Exact transfer-kind proposals / transfer-kind proposals |
| Omission | Candidates without a proposal / candidate denominator |
| Wrong category | Proposed but non-exact, split into ordinary and transfer |
| Correct line reach | Rule-covered spend lines plus exact ordinary proposal spend lines / Truth spend lines |
| Correct amount reach | The equivalent net-spend numerator / Truth net spend |

The public scorer output keeps amount reach as basis points and does not serialize raw money. A
transfer exact match is reported in transfer agreement but never added to correct ordinary reach and
always reports zero auto-write eligibility. Model confidence is not accepted.

## 5. First-red evidence

Before implementation, the new test module failed collection because `ledgerbox.frozen_eval` did not
exist. The implemented suite then passed the following fail-closed counterexamples:

1. one clone missing a candidate;
2. taxonomy or stable row-count mismatch;
3. a clone containing an override or Agent audit;
4. a Base candidate without a Truth label;
5. a duplicate proposal attempting to inflate coverage;
6. a proposal outside the frozen scope;
7. a wrong ordinary category excluded from correct reach;
8. an exact transfer excluded from auto-write and ordinary reach;
9. line and amount denominators remaining independent;
10. identifiers, per-row amounts and external paths absent from public output and errors.

## 6. Commands

Use explicit repository-external placeholders; never commit real paths:

```powershell
python tools/evaluate_frozen_baseline.py preflight `
  --truth <truth-dir> --base <base-dir> --codex <codex-clone> --claude <claude-clone>

python tools/evaluate_frozen_baseline.py score `
  --truth <truth-dir> --base <base-dir> --clone <client-clone> --client codex

python tools/evaluate_frozen_baseline.py compare `
  --truth <truth-dir> --base <base-dir> --codex <codex-clone> --claude <claude-clone>
```

The score command additionally requires exactly one proposal run from the named client, every proposal
still pending, zero effective overrides and no stable-ledger drift from Base.

Ledgerbox stores Out as a non-positive signed aggregate. The scorer normalizes that value to a positive
net-spend magnitude only at the reach adapter boundary; a positive stored Out is rejected. Public output
keeps the amount result as basis points and never serializes the raw numerator or denominator.

## 7. Gate to C4.3

C4.3 may start only while the external run record remains `preflight_passed_models_not_run`, both clones
remain equal to Base, and each client is connected only to its own clone. Both clients must receive the
same operation prompt and official Skill. Submission may add pending proposal audit rows only.

If a preflight check changes, discard no history and do not patch a clone. Stop, explain the aggregate
failure, and rebuild a new Base from the archive.

The completed aggregate comparison is in
[`C4_FROZEN_BASELINE_RESULT.md`](C4_FROZEN_BASELINE_RESULT.md). This document remains the preflight and
scoring contract; the result document does not retroactively change its denominators.
