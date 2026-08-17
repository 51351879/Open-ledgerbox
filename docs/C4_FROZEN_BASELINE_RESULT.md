# C4 frozen baseline result

> Status: **C4.3-C4.4 AUTOMATED COMPARISON COMPLETE; human semantic review and C5 pending**
>
> Reference: one person's frozen final categories, not objective accuracy
>
> Effective category writes: **zero**

## 1. Frozen conditions

Both clients used the same 270-candidate all-dates denominator, 24-category taxonomy, official
`official-classification-v1` Skill, six canonical references and shared operation prompt. Codex and
Claude Code connected only to their own repository-external clones. Each successful run left exactly
one proposal audit whose rows are all pending human review.

The installed clients were Codex CLI 0.141.0 and Claude Code 2.1.207. Neither submitted a client version
or model label in its producer metadata, so this result does not guess one. Claude's first execution was
stopped after an operator time window while the clone still had zero audit rows. The same clean clone,
Skill and prompt were retried with the client tool surface explicitly restricted to project reads, Skill
loading and the five proposal MCP tools; that run completed. No failed or partial audit was deleted.

Codex exposed one scorer integration defect before Claude ran: Ledgerbox stores Out as a non-positive
minor-unit aggregate, while the new reach validator initially expected a positive magnitude. Scoring
stopped without changing the Codex audit. A negative-Out counterexample was added, the adapter was fixed
to normalize the magnitude, and both clients were then scored with the same corrected scorer.

## 2. Aggregate comparison

| Metric | Codex | Claude Code |
|---|---:|---:|
| Candidate denominator | 270 | 270 |
| Proposed | 107 | 123 |
| Proposal coverage | 39.63% | 45.56% |
| Exact frozen-reference agreement | 100 / 107 (93.46%) | 120 / 123 (97.56%) |
| Ordinary agreement | 77 / 83 (92.77%) | 72 / 75 (96.00%) |
| Transfer agreement | 23 / 24 (95.83%) | 48 / 48 (100.00%) |
| Omitted | 163 / 270 (60.37%) | 147 / 270 (54.44%) |
| Wrong ordinary category | 6 | 3 |
| Wrong transfer category | 1 | 0 |
| Correct line reach | 215 / 261 (82.38%) | 211 / 261 (80.84%) |
| Correct net-spend amount reach | 82.75% | 71.06% |
| Auto-write eligible | 0 | 0 |

Amount reach is serialized only as a percentage/basis-point aggregate; raw money is not written into
the repository result. Wrong rows were counted locally without exporting identifiers, descriptors or
per-row amounts. Row-level semantic inspection remains a product-owner review step and must be done in
the local proposal UI without accepting, editing or rejecting either frozen run.

## 3. What the result supports

- Claude Code proposed more of the common denominator and had higher overall, ordinary and transfer
  agreement with the frozen human reference.
- Codex produced more exact ordinary proposals and slightly higher correct line reach. Its correct
  net-spend amount reach was materially higher in this ledger.
- Claude's larger transfer set does not create an automation advantage: every transfer remains pending
  for explicit human approval even when it matches the frozen reference.
- The two clients show different coverage/value trade-offs. A single headline winner would hide this.
- Both clients still made wrong ordinary-category proposals. C4 therefore does not justify silently
  applying all ordinary proposals by default.

## 4. What the result does not support

- It is not objective classification accuracy or a population-level benchmark.
- It does not prove future runs, other banks, other taxonomies or custom Skills will behave the same.
- It does not validate an unrestricted local Agent, general financial analysis or source-code changes.
- It does not approve A7, any migration or an effective-category write path.
- It does not make model confidence a product threshold.

## 5. Integrity and privacy verification

- Truth remained read-only and at zero effective unclassified rows.
- Base and clone ledger revisions, taxonomy, candidate denominator and stable row counts remained equal.
- Both clones remained verifier 9/9.
- Each clone has one proposal run, all proposal rows pending, no triage audit and no category override.
- Public scoring output contains aggregate counts, ratios, client labels and pending state only.
- Raw client output was captured in memory and not persisted; the external run record contains only
  aggregate evidence and is forbidden from Git.

## 6. C5 product decision (2026-08-10)

The product owner completed the local visual/semantic inspection and chose to proceed to A7 with both
Codex and Claude Code supported. After a user explicitly connects and enables a local Agent, automatic
classification will be the default. Submitted transfer proposals may also be applied automatically;
transfer is no longer a permanent manual-only category.

This is a product-policy decision, not a new accuracy claim. The C4 aggregate remains a historical
frozen-reference comparison. The inspected clone may change during post-C4 product use; that does not
rewrite the already committed C4 score.

The implementation contract, omission behavior, provenance requirements and staged DoD are now in
[`A7_AUTOMATIC_CLASSIFICATION_PLAN.md`](A7_AUTOMATIC_CLASSIFICATION_PLAN.md). Existing proposal schema
v1 remains review-only until the versioned A7 path is implemented; the decision does not silently alter
the meaning of the C4 contract.
