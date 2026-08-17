# Automation: what is actually possible, and how to build it yourself

**ledgerbox does not fetch data from your bank, and there are no plans to add
it.** Downloading a statement PDF and running `ledgerbox ingest` is the
supported workflow, not a placeholder for one.

This document exists because "we don't do that" is an unsatisfying answer on its
own. So here is the research instead: what channels exist in 2026, which of them
a private individual can actually use, what each one costs you in security, and
the shape a safe implementation would take — enough that you, or an AI coding
assistant you are working with, can build it yourself and know what you are
signing up for.

> **Provenance and verification date.** Everything in the "official channels"
> and "MCP" sections below is research recorded in
> [`EXECUTION_PLAN.md`](EXECUTION_PLAN.md) §11 and
> [`PROJECT_SUMMARY.md`](PROJECT_SUMMARY.md) §3, **verified in August 2026**.
> Nothing in those sections has been re-verified since. API availability,
> pricing, quotas and the legal situation all change; treat the specifics as
> dated findings and re-check before you rely on any of them. The *reasoning* —
> particularly about credential custody and MCP server trust — ages better than
> the facts.

---

## 1. The official channels, and why none of them work for you

### Chase has no consumer API

`developer.chase.com` is behind JPMorgan enterprise SSO in its entirety. The
JPM Payments self-service registration gives you documentation and a mock
environment; production access goes through a sales process with a four-to-six
week KYC. There is no tier of this that a private individual reading their own
checking account can reach.

### OFX / Direct Connect is dead

`ofx.chase.com` resolves to a CNAME whose target returns NXDOMAIN. Chase has
moved to **EWC+**, in which the OAuth token is held by an aggregator rather than
by you.

The consequence is worth stating precisely, because it is structural rather than
a matter of effort: **you never come into possession of a credential you could
hand to third-party software.** GnuCash, `ofxtools`, and every other OFX client
have no path forward, and no amount of configuration changes that.

### FDX sells the specification, not the data

FDX received CFPB recognition on 2025-01-08 (effective to 2030-01-08). Individual
membership is $99/year; Observer membership is free.

But FDX **operates no data interface at all** — it publishes a standard. And the
standard mandates FAPI 1.0 Advanced plus mutual TLS plus dynamic client
registration, a stack in which **a natural person is not a registrable OAuth
client**. Joining FDX gets you the specification and a seat in the working
group. It does not get you your transactions.

### CFPB Section 1033 is enjoined

The CFPB's open-banking rule — the thing that would have obliged banks to
provide consumer-directed data access — has been **blocked by a court since
2025-10-29** (*Forcht Bank v. CFPB*), and the CFPB has been reconsidering the
rule. Do not build a plan around it landing soon.

### The rest of the aggregator market

| Provider | Verdict |
|---|---|
| **Plaid** | Enterprise. Past the free tier, pricing is undisclosed and goes through sales. Plaid Portal, the consumer-facing product, **cannot export your data** |
| **MX** | Enterprise only |
| **Akoya** | Enterprise only |
| **Finicity** (Mastercard) | Enterprise only |
| **Yodlee** (Envestnet) | Enterprise only |
| **GoCardless Bank Account Data** | **Closed to new registrations**, and covered only the EU anyway |

---

## 2. What actually works today

### Banking: SimpleFIN Bridge, about $15/year

The only option a private individual can sign up for **today** with no company
entity, no KYB, no sales call and no master services agreement. Roughly 25
institutions covered, Chase among them.

What is genuinely good about it:

- **Read-only by protocol design.** There is no payment surface in the protocol.
  A leaked Access URL cannot move money — it can only read. That is a far
  stronger guarantee than "we promise not to."
- **The Access URL lives on your machine.** Not in a vendor's dashboard.
- **You can revoke it unilaterally**, without asking anyone.

Now the costs, stated plainly, because they are real:

- **Your Chase credentials live at MX**, a third-party aggregator, not on your
  machine. This is the important one. "Local-first" in ledgerbox describes
  *where your data comes to rest*; it says nothing about where your credentials
  are held. Using SimpleFIN means accepting aggregator custody of your bank
  login. **No aggregator option available to an individual changes this.** If
  that is unacceptable to you, the answer is the PDF pipeline, and that is a
  legitimate answer.
- **Quota: 24 fetches per day.** Enough for hourly polling and not much more.
- **A single request spans at most 90 days.** Backfilling means paging.
- **The history depth of a first sync is not published.** Plan for 90 days and
  be pleasantly surprised. **Anything older than that still has to come from the
  PDF pipeline** — which is the main reason ledgerbox's PDF path is not
  obsoleted by adding a fetcher, and why the two must produce rows that are
  indistinguishable downstream.

### Brokerage: IBKR Flex Web Service

**The only brokerage channel that can run unattended from cron.** You enable it
yourself in account settings; the token's lifetime is configurable from six
hours to a year; IP allowlisting is supported; two HTTP GETs return XML; and —
unusually — the output includes **lot-level cost basis**.

Everything else:

| Broker | Verdict |
|---|---|
| **Schwab** | Individual developer access is open, but the **refresh token expires hard at 7 days and cannot be renewed**. You must log in through a browser every week, forever. Not automatable in any honest sense |
| **E\*TRADE** | Worse: tokens expire at midnight US Eastern, daily |
| **Fidelity** | No official API for retail |
| **Vanguard** | No official API for retail |
| **Merrill** | No official API for retail |
| **Robinhood** | No official API for equities |

Note that ledgerbox has **no brokerage parser at all** (P4 was deliberately
skipped for lack of real sample statements). The schema models commodities,
lots and cost basis; nothing populates them. A brokerage fetcher would be
building the ingestion side of something whose ledger side is untested.

---

## 3. MCP servers: read this before you install one

If you are wiring an AI assistant to your finances, this section is the one that
matters most. The summary is uncomfortable: **the most popular community
finance MCP servers are also the most dangerous ones.**

### The landscape

- **The official Plaid MCP server hardcodes the sandbox environment.** It cannot
  read real accounts. This is by design, and it is the correct design.
- **Official bank and brokerage MCP servers essentially do not exist.** A search
  across the GitHub organisations for `schwab`, `fidelity`, `InteractiveBrokers`,
  `SnapTrade`, `simplefin`, `ynab`, `actualbudget` and `beancount` returned
  **zero** MCP server repositories.
- **`code-rabi/interactive-brokers-mcp` (205 stars)** asks for `IB_USERNAME`,
  `IB_PASSWORD_AUTH`, and **`IB_TOTP_SECRET`**.

Sit with that last one. The TOTP secret is the seed your authenticator app
derives codes from. **Handing over the TOTP seed makes your two-factor
authentication decorative** — whoever holds it can generate valid codes forever,
and the whole point of the second factor was that it lived somewhere the first
factor did not.

**Stars are a popularity signal, not a security signal.** They measure how many
people found a repository useful enough to bookmark. They do not measure whether
anyone audited it, whether the maintainer is reachable, or what the code does
with the environment variables you gave it.

### What a community MCP server actually is

Stripped of the framing, a locally-installed MCP server is:

**An unsandboxed local process with your credentials in its environment
variables.**

That is the entire security model. From it, four consequences follow:

1. **Every tool call is an exfiltration opportunity.** A legitimate request to
   `api.plaid.com` and an extra POST somewhere else look identical from the
   outside. There is no network boundary between "the call you asked for" and
   "the call you didn't."
2. **Returned data is a prompt-injection channel.** A transaction memo is
   attacker-influenced text — anyone who can send you money can write into it —
   and it lands directly in your model's context. Several of these servers ship
   **write tools enabled by default**, which turns a text-injection problem into
   an action-execution problem.
3. **`npx -y` and `:latest` re-resolve the version on every launch.** You are not
   auditing a version; you are auditing a name, and trusting whoever controls
   that name today and tomorrow.
4. **Nothing is sandboxed.** The process has your user's filesystem access, your
   network, and your environment.

**Do not `npx -y` your brokerage password.**

### The safe shape — which is what this project already is

Separate the process that holds secrets from the process the agent talks to:

```
[fetchers: SimpleFIN URL / IBKR token / PDFs]   ← hold ALL secrets, run on a timer
                    ↓
              ledger.db  (your disk)
                    ↑   read-only connection, PRAGMA query_only = ON
[a ~150-line MCP server you write yourself]     ← zero credentials, zero egress
                    ↑
                your agent
```

The agent never sees a credential, because the process it talks to does not have
one. The worst outcome of a prompt injection is a bad SQL query against a
read-only handle.

ledgerbox already provides the read-only half:
`ledgerbox.db.connection.connect_read_only()` opens with **both** `mode=ro` in
the URI **and** `PRAGMA query_only = ON` — belt and braces, and the docstring
says explicitly that it exists for analytics and for an MCP server.

**There is no official SQLite MCP server in 2026.** The reference implementation
was archived in 2025 and moved to `servers-archived`, where it has been frozen
for roughly fourteen months; the official `src/` tree is down to seven servers
(`everything`, `fetch`, `filesystem`, `git`, `memory`, `sequentialthinking`,
`time`). So those ~150 lines are yours to write: wrap a read-only `sqlite3`
connection, set `query_only`, expose a single `query` tool, return rows.

Given that the alternative is handing a stranger your brokerage password, that
is an excellent trade.

---

## 4. If you build it: notes for you and your AI assistant

Paste this section at whatever is helping you write the code.

### Shape

Add a `src/ledgerbox/fetchers/` package alongside `ingest/`. Define a `Fetcher`
protocol mirroring the existing `Parser` protocol in
`src/ledgerbox/ingest/parsers/base.py`: a stable `fetcher_id`, a
`fetcher_version`, and a method that returns the **same** `ParsedStatement` /
`StatementTxn` / `StatementSummary` structures the PDF parsers return.

Same structures means same downstream, and same downstream means the next point.

### The gate applies identically to fetched data

> **Data pulled from an API must pass every reconciliation check in
> `src/ledgerbox/reconcile/checks.py`. There is no fast path because it "came
> from an API."**

This is the single most important instruction in this document, and the easiest
one to talk yourself out of. The temptation is obvious: JSON from a bank's own
endpoint *feels* authoritative in a way that text scraped off a PDF does not.

It is not. An API response is parsed by code you wrote, against a schema you
inferred, with pagination you implemented, over a window you chose, deduplicated
by a key you designed. Every one of those is a place to be wrong, and being
wrong there produces the same silent, self-consistent, plausible output that
cost the predecessor project a year. The predecessor's parser also felt
authoritative. It rendered every chart without an error.

Concretely, an API fetcher must supply the reconciler with evidence, not just
rows:

- fetch **balances** alongside transactions, and write them as
  `balance_assertion` rows so `verify` can replay the ledger against them
- if the source exposes period subtotals, populate `StatementSummary.components`
  from them
- if it exposes running balances, populate `StatementTxn.balance_minor` — that
  is the strongest check available and it is free
- if the source offers **none** of these, say so out loud. In this codebase a
  block-level check that is *skipped* blocks the ingest, and that behaviour is
  correct: a report that cannot say what it did not check is not evidence.

Set `SOURCE_SYSTEM` to something other than `"pdf"` — `"simplefin"`, `"ibkr"`.
`txn_identity` is unique on
`(account_id, source_system, natural_key, natural_key_version)`, so the same
transaction arriving by two routes will produce two identity rows pointing at
distinct transactions rather than silently colliding. Reconciling those two
views against each other is a feature to build deliberately, not an accident to
allow.

### Credentials

- **Put secrets in the OS keychain, not in `.env`.** Windows Credential Manager,
  macOS Keychain, Secret Service on Linux. A `.env` file gets committed, gets
  copied into a backup, gets read by anything running as you, and shows up in
  `ps` output when it is exported into a child process's environment.
- The fetcher process holds the secret. Nothing downstream of `ledger.db` ever
  sees one. That separation is the whole architecture; do not collapse it for
  convenience.
- Prefer tokens you can revoke unilaterally and scope to read-only. Prefer IP
  allowlisting where offered (IBKR offers it).
- **Never put a TOTP seed in a config file, an environment variable, or a
  third-party tool.** If a service requires one to automate, that service is
  telling you it is not automatable safely.

### Scheduling

- **Stagger your cron entries.** Every fetcher firing at `0 * * * *` is a
  thundering herd against your own quota, and SimpleFIN's is 24 requests a day.
- **Overlap your windows by about 5 days.** Do not fetch exactly "since last
  success": banks backdate, re-post, and correct. An overlap costs nothing
  because the ingest is idempotent by content hash and natural key — re-fetching
  a transaction you already have is a no-op by construction.
- **Remember the 90-day-per-request ceiling** and page the backfill.
- **Alert on failure, loudly.** A fetcher that has been silently failing for
  three weeks produces a ledger that looks complete and is not — precisely the
  failure mode this project exists to prevent. Absence of new rows must be
  distinguishable from absence of new transactions. Record the last successful
  fetch per source and complain when it goes stale.
- **Never let a fetcher write around the gate.** It calls the same pipeline the
  CLI does, or it does not write.

### Testing

Fetchers get tested like parsers: against **recorded fixtures**, not against the
live API. And the same rule zero applies — a recorded API response contains
account numbers, counterparty names and balances. Sanitize it or synthesize it;
never commit the real thing. See
[`ADDING_A_BANK.md`](ADDING_A_BANK.md#the-core-convention-commit-the-text-layer-not-the-pdf).

---

## 5. The short version

- There is no consumer-accessible official channel to Chase. Not through Chase,
  not through OFX, not through FDX, and — while §1033 is enjoined — not through
  regulation.
- **SimpleFIN Bridge (~$15/year) is the one realistic banking option**, at the
  price of aggregator custody of your bank credentials and a probable 90-day
  history horizon.
- **IBKR Flex is the one realistic brokerage option.** Schwab requires a weekly
  manual login; everyone else has nothing.
- **Do not install a community finance MCP server that asks for your password**,
  and absolutely not one that asks for your TOTP seed. Write the ~150-line
  read-only SQLite server yourself.
- **Whatever you fetch goes through the same reconciliation gate as a PDF.** A
  number you cannot prove is not better because an API said it.
- And the PDF pipeline is not going away regardless — it is the only thing that
  reaches back more than 90 days.

---

## Related reading

- [`EXECUTION_PLAN.md`](EXECUTION_PLAN.md) §11 — the original research notes, in
  Chinese, with the verification dates
- [`PROJECT_SUMMARY.md`](PROJECT_SUMMARY.md) §3 — the same conclusions in table
  form, in Chinese
- [`THREAT_MODEL.md`](THREAT_MODEL.md) — what ledgerbox stores and what it does
  not protect against (in Chinese)
- [`ARCHITECTURE.md`](ARCHITECTURE.md) — where a fetcher would plug into the
  existing pipeline
- [`../README.md`](../README.md#automation) — the condensed version
