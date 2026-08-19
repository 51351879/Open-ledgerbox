---
name: ledgerbox-setup
description: Set this freshly cloned Ledgerbox checkout up end to end on Windows -- virtual environment, install, personal classification Skill, MCP registration -- so the user can start uploading bank statements. Use when the user asks to set up, install, or connect this project. Not for classifying transactions (use ledgerbox) or for changing source code.
---

# Set Ledgerbox up

Everything below happens on the user's own Windows machine. One CLI command
does the ordered work; do not re-implement its steps by hand. `--force` and
`--yes` are never yours to add on your own judgement -- the single exception,
with the user's explicit consent first, is spelled out in step 3.

1. Ask the user one question first: **which folder should hold their ledger
   and statements?** Propose a concrete default so that "yes" is a complete
   answer: the checkout's own name with `-data` appended, beside it (a clone at
   `D:\test-ledger` proposes `D:\test-ledger-data`). Any path the user gives
   instead wins. It must be outside any git repository -- the CLI refuses a
   path inside one, so a bad location fails safely -- and never proceed
   without their answer: this folder will hold their financial records for
   years, and its location has to be something they knowingly chose and can
   back up. Do not create anything before they answer.
2. From the checkout root, create the environment and install:

   ```powershell
   python -m venv .venv
   .venv\Scripts\pip install -e .[mcp]
   ```

3. Run the setup command with their answer and this client:

   ```powershell
   .venv\Scripts\ledgerbox.exe setup --client codex --data-dir <their folder>
   ```

   It installs or safely upgrades the personal classification Skill, registers
   the MCP bridge only if that succeeded, and skips registration when this
   client already knows the ledger. An untouched older official install
   upgrades on its own; nothing below applies to it.

   If it stops on a **custom** personal Skill, neither stop dead nor overwrite
   silently. Run `ledgerbox agent doctor --client codex`, show the user
   which files differ, and ask plainly whether to replace their modified Skill
   with the official one -- their edits will be lost. Only on their explicit
   yes, in this conversation, for this replacement:

   ```powershell
   .venv\Scripts\ledgerbox.exe agent install-skill --client codex --force --yes
   ```

   then re-run the setup command above so MCP registration completes. If they
   decline, leave the Skill untouched, register nothing, and stop -- `--force
   --yes` is never yours to run on your own judgement, and other failure
   messages are still read aloud and stopped on, as before.

4. Tell the user to double-click `start-ledgerbox.cmd` (first run: it explains
   the `data-dir.txt` file it wants beside it), open the page it starts, and
   upload statements with **Add statements**. Classification is enabled in the
   sidebar under **Classification settings**, and the light above it turns
   green only when a real MCP session is open.

Supported platform is Windows; on anything else, say so and point at
`docs/AGENT_SETUP.md` instead of improvising. Never read, copy, or summarise
the user's statements or ledger contents during setup.
