---
name: ledgerbox-setup
description: Set this freshly cloned Ledgerbox checkout up end to end on Windows -- virtual environment, install, personal classification Skill, MCP registration -- so the user can start uploading bank statements. Use when the user asks to set up, install, or connect this project. Not for classifying transactions (use ledgerbox) or for changing source code.
---

# Set Ledgerbox up

Everything below happens on the user's own Windows machine. One CLI command
does the ordered work; do not re-implement its steps by hand, and never pass
`--force` or `--yes` to anything here.

1. Ask the user one question first: **which folder should hold their ledger
   and statements?** It must be outside any git repository. Do not invent a
   default; this is where their financial records will live.
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
   client already knows the ledger. If it stops, read its message aloud to the
   user and stop with it: a custom personal Skill is the user's to resolve via
   `ledgerbox agent doctor`, never yours to overwrite.

4. Tell the user to double-click `start-ledgerbox.cmd` (first run: it explains
   the `data-dir.txt` file it wants beside it), open the page it starts, and
   upload statements with **Add statements**. Classification is enabled in the
   sidebar under **Classification settings**, and the light above it turns
   green only when a real MCP session is open.

Supported platform is Windows; on anything else, say so and point at
`docs/AGENT_SETUP.md` instead of improvising. Never read, copy, or summarise
the user's statements or ledger contents during setup.
