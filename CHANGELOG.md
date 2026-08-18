# Changelog

The format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/).
Versions are [PEP 440](https://peps.python.org/pep-0440/); before 1.0.0 any
release may change any interface, and every such change lands here. The
development history behind this file lives on the machine it happened on —
the repository begins at the release below, so this file is the change
narrative, written from what a user can see rather than from commits.

## 0.1.0a1 (preview) — 2026-08-16

First public preview. Windows 11 is the supported platform; see the README's
scope section for exactly what "supported" means here.

### The ledger

- Chase (US) personal checking PDF statements are parsed, reconciled against
  the statement's own printed totals, and only then booked into a local
  SQLite ledger. Unknown layouts and books that do not balance go to a
  review queue; nothing is guessed.
- All amounts are integer minor units end to end. Transaction identity is a
  content hash, so re-ingesting the same file three times changes nothing
  and the ledger can be rebuilt from its own archive.
- Charts decompose the same four headline figures they sit under, for any
  date window; unclaimed lines keep a labelled slice and are never swept
  into an "other". A plain-text beancount export is the escape hatch.

### Classification

- Shipped deterministic rules claim what they can defensibly claim.
- Every human decision teaches a descriptor-template rule, so one answer
  claims the same merchant's identical lines now and at every future
  import. The ledger's owner can also decree standing prefix rules
  (`ledgerbox rules add-prefix`) for facts only they can know.
- Optional bring-your-own-Agent classification connects the user's own
  Codex or Claude Code over a strict local STDIO MCP bridge: versioned
  proposal schemas, atomic application with Agent provenance, whole-run
  withdrawal that preserves later human decisions, bounded multi-round
  runs with captured client logs, and an honest "examined everything and
  declined" outcome. Ledgerbox itself never calls a model and holds no keys.
- Large flows (≥ $1,000 by default) that no person has directly confirmed
  get their own board; confirming one is a real human decision that
  outranks every rule.

### Setup

- `ledgerbox setup --client codex|claude --data-dir <folder>` performs the
  whole first-run chain — data-directory guard, non-forcing personal Skill
  install, MCP registration only after the install succeeded, verification —
  and is idempotent. Checked-in setup Skills let "set this project up" in
  either client resolve to that command.

### Reading it

- The front page is available in Simplified Chinese
  ([README.zh-CN.md](README.zh-CN.md)), with a glossary pinning the words the
  project's honesty depends on. Category IDs, amounts, commands and wire
  values are not translated. Everything under `docs/` is English only, and
  the translated page says so rather than implying otherwise.
- The interface has a language control. English is the default and the page
  is unchanged without a choice; the Simplified Chinese dictionary covers the
  page's own chrome, the connection light, the status strip and the large
  flows board, and anything it does not cover stays in English rather than
  going blank. Category IDs, amounts and dates are substituted into translated
  sentences, never translated. A checked-in `ledgerbox-translate` Skill walks
  the user's own Codex or Claude Code through adding another language.
- Every panel now says the same sentence when the local service is not
  answering. Two of them had a wording of their own.

### Known boundaries

- One bank, one account type, one platform. CSV import, other banks,
  macOS/Linux, and other screen readers than Windows Narrator are not
  supported; the README table is the authority.
- Not yet on PyPI; run from a checkout.
