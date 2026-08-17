# Classification Skill synthetic run

Use only these files:

- `docs/AGENT_CONTRACT.md`
- `.agents/skills/ledgerbox/SKILL.md`
- every Markdown file directly under `.agents/skills/ledgerbox/references/`
- `.agents/skills/ledgerbox/evals/synthetic-cases.jsonl`

Do not read `expected-behaviour.json`, tests, source code, Git history, or any other file. Do not call a live Ledgerbox MCP server. The JSONL rows are synthetic tool-result scenarios; reason about the tool sequence and proposal behavior that the official Skill requires.

Evaluate every case exactly once. Use only category IDs and candidate refs present in that case. Put every candidate ref in one group or in `omitted_refs`, never both. When status is not ready, stop after `ledgerbox_status`. When every candidate is omitted, do not validate or submit an empty proposal. Every submitted proposal remains pending human review, including transfer-kind proposals.

Set `client` from the client currently running this prompt: `codex` for Codex or `claude-code` for Claude Code. Set `skill_origin` to `official` and `skill_version` to `official-classification-v1`.

Output exactly one JSON object with no Markdown fence and no commentary:

```json
{
  "schema_version": 1,
  "skill_origin": "official",
  "skill_version": "official-classification-v1",
  "client": "codex | claude-code",
  "cases": [
    {
      "case_id": "<case id>",
      "outcome": "stopped | submitted | omitted",
      "tools": ["<ordered Ledgerbox tool names>"],
      "groups": [
        {"category_id": "<returned category id>", "candidate_refs": ["<case ref>"]}
      ],
      "omitted_refs": ["<case ref>"],
      "pending_human_review": true,
      "final_summary": "<privacy-safe aggregate summary>"
    }
  ]
}
```

For a submitted case, use the five-line aggregate summary from `docs/AGENT_CONTRACT.md`, with the counts from that synthetic case and `Run: created`. `pending proposals` is the number of distinct candidate refs across all groups, not the number of groups. For an omitted case, use this aggregate shape:

```text
Candidates: <count>; pending proposals: 0; groups: 0; omitted: <count>. No proposal submitted. No effective category changed.
```

For a stopped case, use exactly:

```text
Ledger not ready. No proposal submitted. No effective category changed.
```

Do not output confidence, reasons, category breakdowns, descriptors, amounts, currencies, transaction-like refs, revisions, hashes, or per-candidate explanations in `final_summary`.
