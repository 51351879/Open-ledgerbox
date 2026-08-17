# A7 automatic local-Agent classification plan

> Status: **APPROVED BY C5; A7.0-A7.4 COMPLETE; A7.5 UNDERWAY; WINDOWS REAL-CLIENT, NARRATOR, AND SETUP-TRUTHFULNESS GATES COMPLETE**
>
> Product decision: 2026-08-10
>
> Current runtime: Core proposal schema v2 supports explicit `review_first` and atomic `automatic`;
> schema 15 adds exact job-to-MCP-session and job-to-proposal-run attribution over the schema 14 persistent
> policy-snapshot job state machine. Successful imports atomically enqueue one job when enabled.
> The official Skill uses automatic only for the enabled matching connected client. HTTP and CLI product
> flows now schedule the bounded runner only after a successful import committed a new job.

## 1. Decision

Ledgerbox will support both user-owned **Codex** and **Claude Code**. It will not choose a product-wide
winner: the user selects whichever supported local client is installed. Ledgerbox still has no remote
model client and stores no model key.

After a user explicitly connects and enables a local Agent, the default classification mode will be
**automatic**. An official Skill run may apply every proposal it submits, including a `transfer`
proposal. Transfer is no longer a permanently manual-only category.

This decision changes the product policy; it does not convert the C4 frozen-reference agreement into
objective accuracy. Both C4 clients made ordinary-category errors, so every automatic write must remain
attributable, inspectable and reversible.

## 2. The omission rule

Automatic application and exhaustive coverage are different problems. The classification Skill may
still omit a candidate when the statement facts do not support a defensible category. An omitted
candidate receives no proposal and therefore cannot be auto-applied.

The product must never display `0 pending` as if it meant `0 unclassified`. It must show these states
separately:

| State | Meaning | User destination |
|---|---|---|
| Applied automatically | Agent submitted a category and Core applied it | Agent run audit |
| Waiting for review | Review-first mode has a submitted proposal | Proposal review |
| Needs classification | Agent omitted the candidate or the run failed before submitting it | Unclassified transactions |
| Agent unavailable | The selected local client is not ready | Compact-sidebar connection help |

Changing the transfer policy does not authorize guessing. The Skill may auto-submit a transfer only
when the available transaction facts support that category; payment-rail words alone are not proof.

## 3. Consent and defaults

There are two separate defaults:

1. Without a connected Agent, Ledgerbox remains manual and rule-based. Import never silently launches
   an executable the user has not connected.
2. Once the user explicitly connects an Agent and enables classification, `Auto classify new imports`
   defaults to on. The user may switch to `Review suggestions first` or disconnect the Agent.

The selected client and mode are local settings; readiness and activity are derived evidence, not a
stored claim that a client is connected. A failed Agent run never rolls
back a successful statement import and never turns an omitted row into a catch-all category.

## 4. Core invariants

1. The official Skill receives only the bounded classification tools; it does not read PDFs, arbitrary
   files or arbitrary SQL.
2. Codex and Claude Code use the same versioned Skill, references and Core contract.
3. Core, not the Skill, enforces application mode and the explicit candidate set.
4. Automatic application is atomic with creation of the proposal audit.
5. Effective categories applied by A7 report an honest Agent source, not `set by you`.
6. Every automatic run has a one-action withdrawal that clears only still-matching Agent decisions;
   a later human edit wins and is preserved.
7. Transfer proposals follow the same automatic, audit and withdrawal rules as ordinary categories.
8. Omitted candidates stay unclassified and visible. No `other` or confidence threshold fills them.
9. Rebuild from archive does not reproduce Agent decisions; audit and overrides remain local mutable
   state under the existing rebuild boundary.
10. Real descriptions, amounts, names and identifiers remain outside the repository and public logs.

## 5. Versioning

The existing proposal schema v1 remains the historical review-only contract used by C4. A7 must not
silently change the meaning of a v1 submit.

Proposal schema v2 adds an explicit Core-validated application mode. The official Skill uses v2 only
after the installed Core advertises support. It chooses `automatic` only when the local policy is
enabled, selects the Skill's producer client, and the MCP session identifies that same connected client;
missing, old, disabled or mismatched fields fail closed to `review_first`. Schema v1 remains review-only.

## 6. Milestones

### A7.0 — Truthful zero-pending and omission UX

- Explain beside the proposal panel that it lists submitted suggestions only.
- Explain completed runs as `all submitted suggestions resolved`, not `everything classified`.
- Point users to the unclassified transaction filter.
- Record this C5 decision and supersede the permanent-transfer-approval target.

**DoD:** a completed proposal run cannot be mistaken for exhaustive classification in visible copy or
screen-reader text; focused JS regression passes.

### A7.1 — Honest Agent provenance and forward migration

- Add a forward-only schema migration; do not edit migrations 0001-0010.
- Store human versus Agent override provenance and the originating run where applicable.
- Extend transaction/API/UI source labels to distinguish rule, human and Agent.

**DoD:** old databases migrate without changing existing answers; new Agent answers cannot be rendered
as human answers; repository rebuild and forget tests cover the new state.

### A7.2 — Proposal v2 atomic automatic application

- Accept an explicit v2 application mode.
- In automatic mode, insert the audit, apply every submitted proposal including transfer and complete
  the run in one transaction.
- Preserve review-first mode.
- Keep stale, duplicate, unknown-category, partial-write and lock failures at zero writes.
- Keep per-run withdrawal compare-and-clear semantics.

**DoD:** ordinary and transfer auto-apply tests pass; every injected failure proves audit and effective
categories move together or not at all; withdrawal preserves later human edits.

### A7.3 — Local Agent sidebar for Codex and Claude Code

- Add a compact ledger sidebar for Agent status, setup help, and page navigation; do not add chat.
- Detect Codex and Claude Code independently and let the user select either available client.
- Store the selected client, application mode and enabled state as validated local policy with the safe
  defaults described in §3.
- Show the current ledger, backend health, current MCP activity and last result as distinct states;
  installation or a past session is not Agent connectivity.
- Copy a current-data-dir setup command and fixed run prompt; keep connection help and policy settings
  collapsed, and show badges only for actionable proposal, triage, and review work.

**DoD:** disconnected, one-client, two-client, incompatible, running, completed, partial and failed
states have synthetic tests and real local smoke evidence for both clients.

### A7.4 — Import trigger and exhaustive remainder handoff

- Queue one bounded classification job after a successful import when auto mode is enabled.
- Run the official Skill against current eligible candidates only.
- Show submitted/applied/omitted counts separately.
- Route every omission to `Needs classification`; never hide it because proposal pending is zero.

**DoD:** a synthetic import can finish, trigger either client, auto-apply submitted ordinary and transfer
proposals, and expose all omissions without duplicate runs or source confusion.

### A7.5 — Release and human experience gate

- Full Python/Node/static/data checks and package-content checks.
- Windows real-client smoke for Codex and Claude Code; other platforms remain unverified until CI/smoke
  evidence exists.
- Keyboard, screen-reader copy and visual review of the compact sidebar, automatic completion, failure,
  omission and withdrawal states.

**DoD:** the product owner can identify the connected client, tell applied from omitted, inspect an
automatic transfer decision and withdraw a whole run without using a terminal.

## 7. Implementation status

A7.0-A7.4 are complete and A7.5 is underway. Proposal schema v2 now has strict `review_first`/`automatic` negotiation;
automatic audit creation, ordinary/transfer Agent overrides, outcomes and run completion share one
transaction, and withdrawal preserves later human or other-run decisions. CLI, HTTP API, STDIO MCP and
both official Skill copies negotiate the version and fail closed on mismatches. A7.3 code adds schema 13,
a strict singleton local policy, aggregate-only MCP session/result evidence, independent Codex/Claude Code
detection, and a compact web sidebar that never calls a model. It copies current-ledger setup commands and a fixed prompt.
Synthetic disconnected, installed,
active, completed/partial/failed, strict-write, and browser behavior tests pass. The product owner accepted the
sidebar, and real canonical MCP connection evidence exists for both Codex and Claude Code without treating
installation or a historical session as currently active. A7.4 now has a schema 14 persistent job Core:
one job per import source, a client/mode snapshot, serialized FIFO claiming, exhaustive aggregate outcome
counts, and terminal-state constraints. The ingest transaction now writes the durable job outbox only for
a newly imported statement; duplicate, refused and failed inputs do not queue work, and an enqueue failure
rolls back both database sides. Schema 15 binds an internal job-scoped MCP session and v2 proposal run to that exact running
job, rejecting client, mode, session, or pre-existing-run confusion atomically. A one-job runner now builds
strict isolated Codex/Claude Code invocations, discards process output, and reconciles terminal counts from
durable session/run evidence, including the crash window after proposal commit. HTTP schedules a background
drain after its response and CLI drains after its import summary, only when a new job was committed; duplicate,
refused, failed, or disabled imports do not launch a client. The Agent Center API and compact sidebar now expose the latest
job's queued/running/completed/partial/failed state and candidate/submitted/applied/omitted counts. The latest omission count
is an actionable Transactions badge and `Needs classification` link that clears stale filters and selects unclassified lines.
The Codex Windows real-client gate is complete on an explicitly authorized repository-external synthetic ledger:
16 candidates produced 12 submitted/applied answers and 4 omissions; ordinary and transfer answers carried Agent
attribution, the omission handoff selected exactly 4 unclassified rows, and whole-run withdrawal returned the current
unclassified count to 16 while preserving the historical job outcome. This acceptance also found and closed Windows
npm client-shim resolution and the post-archive database-failure orphan boundary. The package-content gate is also
complete: wheel/sdist map the checkout-canonical Codex/Claude Skills, references and contracts into one internal
read-only workspace; a fresh Windows venv installed the wheel with the MCP extra, ran both console entry points,
and reported both packaged Skills compatible. Installing the wheel alone does not overwrite Skills in arbitrary user
projects. Claude Code 2.1.207 has now passed
an explicitly authorized repository-external synthetic automatic run: 25 candidates produced 19 submitted/applied
answers and 6 omissions; all 19 current decisions have Agent attribution, including 12 ordinary and 7 transfer
decisions. Its first attempt failed before any tool result because the variadic `--allowedTools` option consumed the
prompt; that attempt left zero proposal/override writes, and a tested `--` delimiter fixed the invocation. The product
owner confirmed the retained run's page counts and omission handoff, then withdrew the whole run: 19 answers became
withdrawn, current unclassified returned from 6 to 25, and the historical job remained `25/19/19/6`.
The personal classification-Skill installer/doctor is now complete: it targets Codex and Claude Code's documented
user directories, generates both bundles from one canonical workspace, recognises only package-catalogued prior
official fingerprints as safely upgradeable, treats every unknown or modified tree as custom, previews and confirms
forced replacement, and restores the old directory on promotion failure. A fresh Windows wheel/venv with an isolated
home reported both clients installed/current without touching the real user profile. That installer milestone baseline was
1011 passed / 100 skipped and Node 52 / 52. The product owner also accepted the current Windows Narrator path:
connection state, historical job versus current unclassified counts, withdrawn audit, control names and focus were
all understandable. This evidence does not cover NVDA/JAWS/VoiceOver or other browsers/platforms.

The local A7.5 setup-truthfulness gap is now closed. Agent Center schema v2 reports runner protocol compatibility and
personal `missing/current/outdated/custom` state as separate aggregate facts, without returning personal paths,
manifests, hashes, versions, changed-file names, or contents. The copied PowerShell runs the non-forcing personal
installer first and registers MCP only after success; page reads and clipboard actions perform no install, client
launch, or model call. A custom Skill stops in the UI and points to CLI doctor/manual review; the UI never exposes
`--force --yes`. Legacy schema/field shapes and unknown clients fail closed. The current automated baseline is Python
**1026 passed / 100 skipped** and Node **57 / 57**, with ruff, mypy, repo-data and diff check green. macOS/Linux and
release-package smoke, security/CI, and the remaining release checks remain A7.5 gates.

## 8. Out of scope

- Remote model APIs or Ledgerbox-owned model credentials.
- Giving the financial Skill repository editing, shell, arbitrary file or arbitrary SQL access.
- Calling proposal omission an error when evidence is genuinely insufficient.
- Treating C4 agreement with one frozen human reference as objective accuracy.
- Pushing or publishing the repository without a separate explicit approval.
