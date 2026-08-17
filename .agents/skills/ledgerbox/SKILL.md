---
name: ledgerbox
description: Prepare version-negotiated classifications for verified transactions in the user's local Ledgerbox with their own Codex. Use when the user asks Codex to classify, group, organize, or review bank-statement transactions. Do not use for statement ingestion, remaining-coverage triage, proposal approval, or source-code changes.
---

# Ledgerbox classification

1. Read `docs/AGENT_CONTRACT.md` completely before calling a Ledgerbox tool.
2. Require the local MCP server named `ledgerbox`. If it is unavailable, stop and direct the user to `docs/AGENT_SETUP.md`; never inspect Ledgerbox files as a fallback.
3. Read every official classification reference before preparing a proposal:
   - [workflow](references/workflow.md)
   - [category semantics](references/category-semantics.md)
   - [transfer boundaries](references/transfer-boundaries.md)
   - [grouping and abstention](references/grouping-and-abstention.md)
   - [ambiguous cases](references/ambiguous-cases.md)
   - [privacy and output](references/privacy-and-output.md)
4. Follow the shared contract and official references exactly. Do not add category rules, SQL, filesystem reads, or a second proposal workflow.
5. Follow the contract's version and local-policy negotiation exactly. Use v2 `automatic` only when status says the policy is enabled, selects `codex`, returns `application_mode: automatic`, and the MCP status identifies the connected client as `codex`. Otherwise fail closed to v2 `review_first`; use v1 on Core v1 and stop on an unknown version.
6. Identify the producer client as `codex`. Include a client or model version only when the running client reports it; never guess metadata.
7. After submission, describe the negotiated effect exactly: review-first is pending with no effective change; automatic is applied with Agent attribution.
8. Use the contract's fixed aggregate-only final shape. Never add a category breakdown or any complete, truncated, or abbreviated run/revision ID.
9. Keep classification separate from requests to modify Ledgerbox source code. Codex Cloud contribution boundaries are in `docs/AGENT_SETUP.md`.
