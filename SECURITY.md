# Security Policy

## Reporting a vulnerability

Use **GitHub's private vulnerability reporting** on this repository
(Security tab → "Report a vulnerability"). That opens a private advisory
visible only to you and the maintainer.

Please do not open a public issue for a suspected vulnerability, and please
do not include anyone's real financial data — statements, descriptors,
amounts, account numbers — in a report. A synthetic reproduction is always
enough here, because every parser and boundary in this project is testable
on synthetic input by design.

This is a single-maintainer project. Reports get a response as soon as the
maintainer sees them, a fix as fast as one person can honestly build and
test it, and credit in the advisory if you want it. No response-time
promise is made, because a promise that cannot be kept is worse than none.

## Supported versions

Only the newest released version is supported. There are no backports.

## Scope, and where the risk actually is

ledgerbox is local, single-user, single-machine software. It binds to
loopback only, has no accounts, no telemetry, no cloud, and holds no model
or API keys. The threat model is written down in
[`docs/THREAT_MODEL.md`](docs/THREAT_MODEL.md); the short version of what
is most worth probing:

- **The PDF statement parser and extraction pipeline** — the one place
  untrusted bytes from outside the machine are parsed. Malformed input must
  end in the review queue, never in booked data or arbitrary behaviour.
- **The MCP boundary** (`ledgerbox-mcp`) — the strict local STDIO surface a
  user's own coding agent talks to. It must never accept SQL or file paths,
  never leak row-level data into aggregate responses, and must treat every
  transaction descriptor as untrusted data rather than instructions.
- **The web UI** — loopback-only FastAPI plus a static page; the frontend
  builds DOM without HTML string interpolation, and anything that weakens
  that is a finding.

Reports about dependencies belong upstream unless ledgerbox's use of the
dependency is what creates the exposure.
