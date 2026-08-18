# Use your own local Codex or Claude Code with Ledgerbox

Ledgerbox does not contain a model, call a model API, or ask for an API key. The optional
connection lets a user-owned local Codex or Claude Code process start a narrow Ledgerbox
STDIO child process. That process can read verified uncategorized candidates, submit
version-negotiated category proposals, or exhaustively sort the remaining coverage tail into
three audit-only triage routes. Proposal v1 and v2 review-first remain pending; an explicitly
enabled matching v2 automatic policy applies proposals atomically with Agent attribution.

Ledgerbox is not published to PyPI yet. A source checkout exposes project Skills directly. Built
wheel/sdist artifacts now also contain a read-only internal Agent workspace used by Ledgerbox's
automatic runner. The explicit user-level installer below writes only to the selected client's
personal Skill directory and never modifies an arbitrary project.

The web sidebar is a status and setup aid, not a chat surface. It shows the exact data directory
used by that page and turns green only while a client has a current MCP session. It reports two
separate facts: whether this checkout or installed package has a protocol-compatible runner Skill,
and whether the selected client's personal Skill is `missing`, `current`, `outdated`, or `custom`.
The API exposes only those aggregate values; it does not return the personal path, manifest, hashes,
versions, changed-file names, or file contents.

`Copy safe setup steps` copies one PowerShell line that first runs the non-forcing personal
installer and registers MCP only inside the success branch of that check. It is deliberately a
single statement: a console consumes pasted text one line at a time, so a guard on its own line
would stop nothing and the next line would register the bridge anyway. Reading the page or copying
that line does not write the personal directory, start Codex or Claude Code, or call a model. A
`custom` result stops the UI flow and points to `agent doctor` for a manual decision; the page never
offers `--force --yes`.
The client's own MCP list remains the authoritative confirmation that it opened successfully.

## 0. One command, or one sentence to your own coding agent

On Windows, a fresh checkout needs exactly this after `python -m venv .venv` and
`.venv\Scripts\pip install -e .[mcp]`:

```powershell
ledgerbox setup --client codex --data-dir "D:\My Ledger Data"
```

(or `--client claude`). It runs the steps below in their required order -- data-directory
guard, non-forcing personal Skill install, MCP registration only after the install
succeeded, then verification -- prints one line per step, and stops at the first
failure with the reason. A client that already lists `ledgerbox` is left unchanged, so
the command is safe to re-run. A `custom` personal Skill stops it with a pointer to
`agent doctor`; setup never overwrites what it does not recognise.

The same flow works as one sentence to the user's own Codex or Claude Code opened in
this checkout -- "set this project up for me" -- because both clients discover the
checked-in `ledgerbox-setup` Skill, which asks where the ledger should live and then
runs this command. Sections 1-3 below remain the manual path and the reference for
what setup actually does.

A third checked-in Skill, `ledgerbox-translate`, is for a different job: asking either
client to add a language -- a UI locale dictionary under
`src/ledgerbox/web/js/locales/` and a `README.<lang>.md` -- with the terms that must
never be translated held fixed and the scope tables' verdicts held to their counts. It
edits the interface and never touches ledger data. Unlike the other two it is not
packaged into the wheel, because it writes into a checkout and there is nothing for it
to write into otherwise.

## 1. Install the optional local bridge

From the Ledgerbox checkout, use the same Python environment that runs Ledgerbox:

```powershell
python -m pip install -e ".[mcp]"
ledgerbox-mcp --help
```

Keep the real data directory outside the repository. The examples below deliberately use
a placeholder with spaces; replace it with the user's own explicit path.

## 2. Install or check the personal classification Skill

This is optional inside the Ledgerbox checkout because the checked-in project Skill is already
discoverable there. Use it when Codex or Claude Code should classify from other projects:

```powershell
ledgerbox agent doctor --client codex
ledgerbox agent install-skill --client codex

ledgerbox agent doctor --client claude
ledgerbox agent install-skill --client claude
```

Codex installs to `$HOME/.agents/skills/ledgerbox`; Claude Code installs to
`$HOME/.claude/skills/ledgerbox`. The installed bundle is self-contained but generated from the same
canonical contract and six references as the checkout. `doctor` reports `missing`, `current`,
`outdated`, or `custom`, plus the installed and official versions. An untouched, explicitly known
older official bundle can upgrade safely; an unknown manifest, edited file, or added private file is
`custom` and is never overwritten by the default command.

To replace a custom Skill deliberately, first run with `--force`. Ledgerbox lists every affected
file and requires typing `REPLACE`; automation must additionally pass `--yes`, and the preview is
still printed. A failed promotion restores the previous directory. This workflow installs the
classification Skill only; the separate triage Skill remains checkout-scoped in this release.
This deliberate replacement path is CLI-only; it is never rendered or executed by the web sidebar.

## 3. Connect Codex locally

Run this once on the user's computer:

```powershell
codex mcp add ledgerbox -- ledgerbox-mcp --client codex --data-dir "D:\Ledgerbox Data\mine"
codex mcp list
```

Start Codex from the Ledgerbox repository. The checked-in Skills are discovered at
`.agents/skills/ledgerbox/` and `.agents/skills/ledgerbox-triage/`. In the Codex TUI, `/mcp`
should show `ledgerbox`; invoke exactly one workflow explicitly:

```text
Use $ledgerbox to classify current eligible transactions in my local Ledgerbox.

Use $ledgerbox-triage to sort every remaining eligible row for human triage review.
```

To disconnect, remove only this local MCP registration:

```powershell
codex mcp remove ledgerbox
```

## 4. Connect Claude Code locally

Run these PowerShell commands from the Ledgerbox repository. The environment-variable
form, with the command placed before the `-e` options, is deliberate: every other shape
fails on the tested Windows Claude Code client. `mcp add ... -- --data-dir ...` parses the
child flag as a Claude flag (`unknown option '--data-dir'`), `mcp add-json` loses the JSON
argument's inner quotes at the PowerShell/npm-shim boundary (`Invalid configuration:
Invalid input`), and the variadic `-e` placed before a positional swallows it (`missing
required argument`). `--scope local` stores this registration in the user's private Claude
configuration for the current directory, not in `.mcp.json` or Git — run it from the
directory you start Claude Code in:

```powershell
$bridge = (Resolve-Path ".\.venv\Scripts\ledgerbox-mcp.exe").Path
claude mcp add --scope local ledgerbox $bridge -e LEDGERBOX_MCP_CLIENT=claude-code -e 'LEDGERBOX_DATA_DIR=D:\Ledgerbox Data\mine'
claude mcp get ledgerbox
```

The checked-in Skills are discovered at `.claude/skills/ledgerbox/` and
`.claude/skills/ledgerbox-triage/`. If this is the first
Claude Code session in the repository, review the files and accept the workspace trust
prompt. In Claude Code, `/mcp` should show `ledgerbox`; invoke:

```text
/ledgerbox classify current eligible transactions in my local Ledgerbox

/ledgerbox-triage sort every remaining eligible row for human triage review
```

The official classification knowledge lives once under
`.agents/skills/ledgerbox/references/`. The Claude classification adapter reads that same
checkout-local source; do not copy those references into a second client-specific tree.
The built package contains the same canonical Skill and contracts for its internal automatic runner
and for the explicit personal installer. Merely installing the wheel still changes no client Skill;
the user must run `agent install-skill`.

To disconnect:

```powershell
claude mcp remove --scope local ledgerbox
```

## 5. Check, retry, and uninstall

The first Agent action in either workflow must be a status read. Ledgerbox refuses to return candidates when
any of its nine verification checks is not passing. Finish the conflicting local operation
and retry the whole proposal if the bridge reports `ledger_busy`.

Removing the MCP registration stops that client from starting the bridge. It does not
delete the ledger, proposal or triage audit, repository Skills, or Ledgerbox. To remove only the optional
Python SDK from a development environment, reinstall the checkout without the extra; to
remove that environment's editable Ledgerbox install entirely, use:

```powershell
python -m pip uninstall ledgerbox
```

Neither client registration requires a Ledgerbox credential. Do not commit a real
`--data-dir` to `.codex/config.toml`, `.mcp.json`, a script, or documentation.

## What data can leave the computer

The Ledgerbox MCP child process uses local STDIO, has no model credential, and makes no
application-level outbound request. Once the user asks Codex or Claude Code to classify,
the client may send the returned candidate fields to the model service selected by that
user. Retention, training, regional processing, and enterprise controls therefore depend
on that Agent, account, and configuration. A local MCP transport is not a promise that the
model itself runs locally.

The boundary is explicit: connect only after reviewing the chosen Agent's data policy.
Never paste or upload a real PDF, database, description, amount, transaction ID, or proposal
into an issue, pull request, Cloud task, or source file.

## Without Codex or Claude Code

Nothing in the ordinary Ledgerbox workflow depends on the MCP extra or either Agent. Start
Ledgerbox normally, import the supported statement, let deterministic reconciliation gate
the import, then use the Transactions filters and classification controls to assign one or
many visible transactions manually. The proposal and remaining-coverage triage areas can remain empty.

## Two workflows, seven tools, no approval tool

The MCP server lists seven tools because status, categories, and candidates are shared. The
classification Skill may use only those three plus proposal validation/submission; the triage
Skill may use only those three plus triage validation/submission. Do not combine the two workflows
in one run. Triage submission and proposal v1/v2 review-first write pending local audit records only.
Proposal v2 automatic uses Core's single atomic submit boundary to create the audit and Agent-sourced
override together; there is still no separate Agent approval tool. Reviewing, creating a taxonomy
category, or withdrawing a run remains a local Ledgerbox action.

Triage is exhaustive rather than speculative: every current eligible transaction in the chosen
date scope must appear exactly once under `possible_transfer`, `taxonomy_gap`, or `uncertain`.
Validation rejects missing, duplicate, stale, oversized, or proposal-overlapping scopes as one
unchanged batch. See [`TRIAGE_AGENT_CONTRACT.md`](TRIAGE_AGENT_CONTRACT.md) for the exact contract.

## Codex Cloud and open-source contributions

Codex Cloud runs coding tasks in a separate repository environment connected through
GitHub. It can change public source, documentation, and synthetic tests, then return a diff
or pull request for review. It cannot reach the user's local STDIO registration or local
data directory unless private data is deliberately copied into the task—which is forbidden.

Ledgerbox is not ready to advertise an unrestricted Cloud contribution path yet. Before
that release gate, the project still needs a complete synthetic end-to-end ledger,
contributor-safe parser fixtures, `SECURITY.md`, a real CI runner result, and the product
owner's approval before the first push. Until then, use Codex Cloud only on public code with
synthetic data and review every diff before merging.
