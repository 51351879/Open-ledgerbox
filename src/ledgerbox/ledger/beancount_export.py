# SPDX-License-Identifier: AGPL-3.0-or-later
"""Beancount export: the plain-text escape hatch.

SQLite is the system of record. This module writes the same ledger out as a
beancount file, because the third backup — the one everybody skips — is the one
that recovers from *your own software being wrong*. A ledger you can only read
through ledgerbox is a ledger exactly as trustworthy as ledgerbox.

**Never ``import beancount``.** beancount is GPL-2.0-only. ledgerbox is
AGPL-3.0-or-later and means to keep the option of a more permissive licence
open, so a GPL-2.0-only import is not available to it. What is borrowed here is
the *file format* — a syntax, not a program. Validation runs the ``bean-check``
executable in a subprocess (see ``tests/test_beancount_export.py``), which is
arm's-length use of a separate program rather than linking against it. There is
a test that greps this file for the import, because a rule nobody checks is a
comment.

This is a financial-ledger export, not a backup of product workflow history.
It carries effective categories but not ``agent_proposal_run`` or
``agent_category_proposal`` rows, and it cannot preserve whether a category was
accepted unchanged, edited, rejected, or later withdrawn. Keep a database
backup for that local audit trail; ``archive/`` cannot reconstruct it either.

Three rules follow from what this file is for:

* **Integers only.** Amounts stay counts of minor units until the final
  ``str.format``; :func:`~ledgerbox.money.decimal_str` does the base-100 split
  with ``divmod``. No ``float`` and no ``Decimal`` touches a number here.
* **Byte-identical on every run.** Every ordering is an explicit ``ORDER BY`` or
  an explicit ``sorted``; every date written is either a stored date or a fixed
  constant. Nothing reads the clock. Two exports of one ledger diff clean, which
  is what makes the file worth committing to git.
* **Refuse rather than approximate.** Anything the schema can hold but this
  exporter cannot render exactly — a security lot, a per-leg settlement date, a
  currency that is not two-decimal — raises :class:`BeancountExportError`. A
  beancount file quietly missing a share position is worse than no file at all.

Balance dates are shifted, and that shift is the subtle part
-----------------------------------------------------------

``balance_assertion.as_of`` is a **closing** balance: the account held this much
after every transaction dated on or before ``as_of``. That is why
:mod:`ledgerbox.ledger.posting` dates a statement's *opening* assertion at
``period_start - 1 day``.

Beancount's ``balance`` directive is an **opening** check: it is evaluated
before any transaction dated on the directive's own date. The two definitions
line up under exactly one shift::

    stored:  as_of = D            covers  txn.date <= D
    written: balance at D + 1     covers  txn.date <  D + 1   (identical set)

so the directive is dated ``as_of + 1``. Dating it at ``as_of`` would compare a
closing balance against that day's opening position: correct-looking on any day
with no transactions, wrong on every other — the worst possible failure shape.
``bean-check`` confirms both halves of this in the test module.

Opening balances, and the ``pad`` fallback
------------------------------------------

Beancount replays a ledger from zero, so money that predates the oldest
statement has to be stated somewhere or every printed balance fails. The ledger
states it: :func:`ledgerbox.db.repo.sync_opening_entry` books a real transaction
against ``Equity:Opening-Balances``, dated at the earliest assertion. Nothing
special happens to it here — it is a transaction, it is exported as one, and the
balance directives then check against a ledger that starts where the account
did.

The ``pad`` in :func:`_pad_for` is the fallback for a database that has
assertions but no such entry (one written before that function existed, or an
account it did not reach). It is emitted only where it is needed *and* cannot
hide anything, which for a ledger with opening entries means never — a pad
would be suppressed by its own second condition, since the opening entry is a
posting dated on the earliest assertion.

Either way, no opening figure is invented: whichever mechanism supplies it, the
number is the one the bank printed.
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from datetime import date, timedelta
from pathlib import Path

from ..db.migrate import schema_version
from ..fsutil import atomic_write_text
from ..money import decimal_str

#: Filename used when :func:`export_beancount` is handed a directory.
EXPORT_FILENAME = "ledger.beancount"

#: Date for ``commodity`` directives and for accounts nothing references.
#: A constant, never ``today``: today's date would make the export differ from
#: itself overnight and destroy the only property that makes it diffable.
EPOCH_DATE = "1970-01-01"

#: Seeded by migration 0003; the other side of every opening ``pad``.
OPENING_BALANCES_ACCOUNT_ID = "equity:opening-balances"

#: Beancount's five account types. The names are configurable in beancount via
#: ``option "name_assets"`` and friends; ledgerbox does not configure them, so
#: an account name outside this set is a name beancount will not accept.
ACCOUNT_ROOTS = ("Assets", "Liabilities", "Equity", "Income", "Expenses")

#: ``decimal_str`` splits at 100 by construction, so a currency with any other
#: number of minor digits would be rendered off by a factor of ten.
SUPPORTED_SCALE = 2

TITLE = "ledgerbox"

#: Marks ``txn.is_transfer``. The tag matters because
#: :func:`~ledgerbox.db.repo.ledger_totals` excludes flagged transactions from
#: income and expense — dropping it would make the export unable to reproduce
#: the ledger's own headline numbers.
TRANSFER_TAG = "transfer"

_RULE = ";; " + "-" * 74


class BeancountExportError(RuntimeError):
    """The ledger holds something this exporter cannot render faithfully.

    Raised instead of writing an approximation. Every message names the row, so
    the fix is a schema-aware change here rather than a guess at the file.
    """


# ---------------------------------------------------------------------------
# rows, read once and typed
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class _Account:
    id: str
    name: str
    currency: str


@dataclass(frozen=True, slots=True)
class _Txn:
    id: str
    day: str
    payee: str | None
    narration: str | None
    flag: str
    is_transfer: bool


@dataclass(frozen=True, slots=True)
class _Posting:
    txn_id: str
    seq: int
    account_id: str
    amount_minor: int
    currency: str


@dataclass(frozen=True, slots=True)
class _Balance:
    account_id: str
    as_of: str
    commodity_id: str
    amount_minor: int


# Explicit ORDER BY on every read. SQLite's row order without one is an
# implementation detail of the query planner, and "deterministic in practice"
# is the property this export exists to not rely on.

_ACCOUNT_SQL = "SELECT id, name, currency FROM account ORDER BY name, id"

_COMMODITY_SQL = "SELECT id, kind, scale FROM commodity ORDER BY id"

#: ``is_transfer`` comes from ``v_txn_transfer``, not from ``txn`` — the view is
#: the one definition of the answer, folding the rule-derived flag together with
#: a person's override (migration 0005). Reading the raw column here would tag a
#: transfer the rules found and miss one a person marked, and the export would
#: stop reproducing the ledger's own income and expense figures for exactly the
#: rows a human had looked at. That is the third place this concept could have
#: grown a second definition; ``docs/STATUS.md`` §5.29 is what the first two cost.
_TXN_SQL = """
SELECT t.id, t.date, t.payee, t.narration, t.flag, vt.is_transfer
FROM txn t
JOIN v_txn_transfer vt ON vt.txn_id = t.id
WHERE t.superseded_by IS NULL
ORDER BY t.date, COALESCE(t.narration, ''), t.id
"""

# `superseded_by IS NOT NULL` rows are corrections that have been replaced; they
# are kept in the database as evidence and must never reach a report.
_POSTING_SQL = """
SELECT p.txn_id, p.seq, p.account_id, p.amount_minor, p.currency,
       p.date, p.quantity_scaled, p.commodity_id, t.date
FROM posting p
JOIN txn t ON t.id = p.txn_id
WHERE t.superseded_by IS NULL
ORDER BY p.txn_id, p.seq
"""

_BALANCE_SQL = """
SELECT account_id, as_of, commodity_id, amount_minor, quantity_scaled
FROM balance_assertion
ORDER BY as_of, account_id, commodity_id
"""


def _load_accounts(conn: sqlite3.Connection) -> list[_Account]:
    return [
        _Account(id=str(row[0]), name=str(row[1]), currency=str(row[2]))
        for row in conn.execute(_ACCOUNT_SQL).fetchall()
    ]


def _load_commodities(conn: sqlite3.Connection) -> list[tuple[str, str, int]]:
    return [
        (str(row[0]), str(row[1]), int(row[2])) for row in conn.execute(_COMMODITY_SQL).fetchall()
    ]


def _load_txns(conn: sqlite3.Connection) -> list[_Txn]:
    return [
        _Txn(
            id=str(row[0]),
            day=str(row[1]),
            payee=None if row[2] is None else str(row[2]),
            narration=None if row[3] is None else str(row[3]),
            flag=str(row[4]),
            is_transfer=bool(row[5]),
        )
        for row in conn.execute(_TXN_SQL).fetchall()
    ]


def _load_postings(conn: sqlite3.Connection) -> dict[str, list[_Posting]]:
    """Grouped by transaction, each group already in ``seq`` order.

    Leg order is part of the meaning: seq 0 is the bank's own side, the one
    whose sign matches what the statement printed. Reordering the legs would
    still balance and would still be wrong.
    """
    grouped: dict[str, list[_Posting]] = {}
    for row in conn.execute(_POSTING_SQL).fetchall():
        txn_id = str(row[0])
        posting_day, quantity_scaled, commodity_id, txn_day = row[5], row[6], row[7], str(row[8])

        # Units of a security are not dollars. The schema keeps them in separate
        # columns on purpose, and rendering only `amount_minor` would export a
        # share purchase as a bare cash movement with the shares deleted.
        if quantity_scaled is not None or commodity_id is not None:
            raise BeancountExportError(
                f"posting {txn_id}:{int(row[1])} carries a commodity quantity "
                f"(quantity_scaled={quantity_scaled!r}, commodity_id={commodity_id!r}); "
                f"beancount export of holdings is not implemented"
            )
        # Beancount dates the transaction, not the leg. A leg that settles on a
        # different day would silently move to the transaction's date here.
        if posting_day is not None and str(posting_day) != txn_day:
            raise BeancountExportError(
                f"posting {txn_id}:{int(row[1])} settles on {posting_day!r} but its "
                f"transaction is dated {txn_day!r}; beancount has no per-leg date"
            )

        grouped.setdefault(txn_id, []).append(
            _Posting(
                txn_id=txn_id,
                seq=int(row[1]),
                account_id=str(row[2]),
                amount_minor=int(row[3]),
                currency=str(row[4]),
            )
        )
    return grouped


def _load_balances(conn: sqlite3.Connection) -> list[_Balance]:
    rows: list[_Balance] = []
    for row in conn.execute(_BALANCE_SQL).fetchall():
        account_id, as_of, commodity_id = str(row[0]), str(row[1]), str(row[2])
        amount_minor, quantity_scaled = row[3], row[4]
        if quantity_scaled is not None:
            raise BeancountExportError(
                f"balance assertion for {account_id} on {as_of} asserts a quantity "
                f"({quantity_scaled!r}); beancount export of holdings is not implemented"
            )
        if amount_minor is None:
            raise BeancountExportError(
                f"balance assertion for {account_id} on {as_of} ({commodity_id}) has no "
                f"amount; refusing to write a balance directive with nothing in it"
            )
        rows.append(
            _Balance(
                account_id=account_id,
                as_of=as_of,
                commodity_id=commodity_id,
                amount_minor=int(amount_minor),
            )
        )
    return rows


# ---------------------------------------------------------------------------
# formatting primitives
# ---------------------------------------------------------------------------


def quote(text: str) -> str:
    """Wrap *text* as a beancount string literal.

    Backslash first, then the quote — the other order would double-escape the
    backslash it had just inserted. Newlines and tabs get the same treatment
    rather than being dropped: a descriptor is the bank's bytes, and an export
    that silently reflows them is an export that cannot be diffed against the
    source. beancount unescapes all four.
    """
    escaped = (
        text.replace("\\", "\\\\")
        .replace('"', '\\"')
        .replace("\n", "\\n")
        .replace("\r", "\\r")
        .replace("\t", "\\t")
    )
    return f'"{escaped}"'


def next_day(iso_day: str) -> str:
    """``'2025-01-31'`` → ``'2025-02-01'``. See the module docstring."""
    return (date.fromisoformat(iso_day) + timedelta(days=1)).isoformat()


@dataclass(frozen=True, slots=True)
class _Columns:
    """Column widths, derived from the data so two exports agree.

    Alignment is cosmetic; *stable* alignment is not. Widths computed from the
    widest row mean one new transaction cannot re-indent the whole file and turn
    a one-line change into a whole-file diff — as long as it is not the widest.
    """

    account: int
    amount: int

    def money(self, account_name: str, amount_minor: int, currency: str) -> str:
        return (
            f"{account_name:<{self.account}}  {decimal_str(amount_minor):>{self.amount}} {currency}"
        )


def _columns(names: list[str], amounts: list[int]) -> _Columns:
    return _Columns(
        account=max((len(name) for name in names), default=1),
        amount=max((len(decimal_str(value)) for value in amounts), default=1),
    )


# ---------------------------------------------------------------------------
# opening balances
# ---------------------------------------------------------------------------


def _pad_for(
    balances: list[_Balance],
    first_posting_day: dict[str, str],
) -> dict[str, str]:
    """``{account_id: pad date}`` for the accounts that need one.

    A pad is written only where it is needed *and* cannot hide anything:

    * **non-zero.** beancount rejects a pad it did not have to use ("Unused Pad
      entry"), so a ledger opening at exactly $0.00 must not get one — and does
      not need one, since an account with no prior postings already reads zero.
    * **nothing before it.** If a posting is dated on or before the earliest
      assertion, that assertion is a genuine check of booked rows and padding it
      would absorb a real discrepancy. Left unpadded, it is verified for real;
      if it then fails, that is a finding, not a formatting problem.

    Keyed on the earliest assertion per account, which for a statement-derived
    ledger is ``period_start - 1 day`` of the oldest statement — the balance
    before ledgerbox has any evidence at all.
    """
    earliest: dict[str, _Balance] = {}
    at_earliest: dict[str, int] = {}
    for row in balances:  # already ordered by (as_of, account_id, commodity_id)
        current = earliest.get(row.account_id)
        if current is None or row.as_of < current.as_of:
            earliest[row.account_id] = row
            at_earliest[row.account_id] = 1
        elif row.as_of == current.as_of:
            at_earliest[row.account_id] += 1

    pads: dict[str, str] = {}
    for account_id in sorted(earliest):
        row = earliest[account_id]
        if at_earliest[account_id] > 1:
            # One pad cannot open two commodities at once, and guessing which
            # one it means would be a coin flip over somebody's money.
            raise BeancountExportError(
                f"account {account_id} declares balances in more than one commodity on "
                f"{row.as_of}; multi-commodity opening balances are not implemented"
            )
        if row.amount_minor == 0:
            continue
        opened_before = first_posting_day.get(account_id)
        if opened_before is not None and opened_before <= row.as_of:
            continue
        pads[account_id] = row.as_of
    return pads


# ---------------------------------------------------------------------------
# rendering
# ---------------------------------------------------------------------------


def _header(conn: sqlite3.Connection) -> list[str]:
    version = schema_version(conn)
    return [
        ";; Generated by ledgerbox. DO NOT EDIT THIS FILE BY HAND.",
        ";;",
        f";; Exported from a ledger.db at schema version {version:04d}. This file is a",
        ";; derived view; the SQLite ledger is the system of record. Anything edited",
        ";; here is lost the next time `ledgerbox export beancount` runs.",
        ";;",
        ";; It exists so the ledger survives ledgerbox being wrong: the numbers below",
        ";; can be checked with bean-check, fava, or a text editor, none of which share",
        ";; any code with the program that produced them.",
        ";;",
        ";; One conversion is applied and it is not cosmetic: balance directives",
        ";; are dated one day AFTER the stored assertion. ledgerbox records a balance",
        ";; as of the END of a day; beancount checks a balance at the START of its",
        ";; date. The same instant is `as_of + 1`.",
        ";;",
        ";; The `Opening balance` transaction against Equity:Opening-Balances is the",
        ";; money that predates the oldest statement. It is a real row in the ledger,",
        ";; not something invented here. Older databases that lack it get an",
        ";; equivalent `pad` directive in the section below instead.",
    ]


def _options() -> list[str]:
    return [f'option "title" {quote(TITLE)}']


def _commodity_block(commodities: list[tuple[str, str, int]]) -> tuple[list[str], list[str]]:
    """``(option lines, commodity directives)``.

    Every commodity of kind ``currency`` is declared as an operating currency;
    P0 has exactly one. The directive date is :data:`EPOCH_DATE` — a commodity
    directive is a declaration, not an event, and dating it "today" would be the
    single line that changes between two otherwise identical exports.
    """
    options = [
        f'option "operating_currency" {quote(code)}'
        for code, kind, _scale in commodities
        if kind == "currency"
    ]
    directives = [f"{EPOCH_DATE} commodity {code}" for code, _kind, _scale in commodities]
    return options, directives


def _block(title: str, body: list[str]) -> list[str]:
    header = [_RULE, f";; {title}", _RULE]
    return [*header, "", *body] if body else [*header, ";; (none)"]


def render_beancount(conn: sqlite3.Connection) -> str:
    """The whole ledger as one beancount document.

    Pure with respect to the clock and the filesystem: same database in, same
    string out, byte for byte.
    """
    accounts = _load_accounts(conn)
    by_id = {account.id: account for account in accounts}
    for account in accounts:
        _check_account_name(account.name)
    commodities = _load_commodities(conn)
    scales = {code: scale for code, _kind, scale in commodities}

    txns = _load_txns(conn)
    postings = _load_postings(conn)
    balances = _load_balances(conn)

    _check_scales(scales, postings, balances)

    # `first_seen` drives the `open` directives. Beancount rejects any reference
    # to an account before its open date, and a pad or a balance can legitimately
    # be the earliest reference — the first statement's opening assertion always
    # is. Taking strictly "the date of the first transaction", as a naive reading
    # would, produces a file bean-check refuses to load.
    first_seen: dict[str, str] = {}
    first_posting_day: dict[str, str] = {}

    def note(account_id: str, day: str) -> None:
        known = first_seen.get(account_id)
        if known is None or day < known:  # ISO-8601 sorts as text
            first_seen[account_id] = day

    for txn in txns:
        for posting in postings.get(txn.id, ()):
            note(posting.account_id, txn.day)
            known = first_posting_day.get(posting.account_id)
            if known is None or txn.day < known:
                first_posting_day[posting.account_id] = txn.day

    pads = _pad_for(balances, first_posting_day)
    if pads and OPENING_BALANCES_ACCOUNT_ID not in by_id:
        raise BeancountExportError(
            f"account {OPENING_BALANCES_ACCOUNT_ID!r} is missing, so opening balances "
            f"for {sorted(pads)} have nowhere to come from"
        )
    for account_id, pad_day in pads.items():
        note(account_id, pad_day)
        note(OPENING_BALANCES_ACCOUNT_ID, pad_day)
    for row in balances:
        note(row.account_id, next_day(row.as_of))

    columns = _columns(
        names=[by_id[posting.account_id].name for legs in postings.values() for posting in legs]
        + [by_id[row.account_id].name for row in balances],
        amounts=[posting.amount_minor for legs in postings.values() for posting in legs]
        + [row.amount_minor for row in balances],
    )

    currency_options, commodity_directives = _commodity_block(commodities)

    lines: list[str] = [
        *_header(conn),
        "",
        *_options(),
        *currency_options,
        "",
        *_block("Commodities", commodity_directives),
        "",
        *_block("Accounts", _open_directives(accounts, first_seen)),
        "",
        *_block("Opening balances", _pad_directives(pads, by_id)),
        "",
        *_block(f"Transactions ({len(txns)})", _txn_directives(txns, postings, by_id, columns)),
        "",
        *_block(
            f"Balance assertions ({len(balances)})", _balance_directives(balances, by_id, columns)
        ),
    ]
    return "\n".join(lines) + "\n"


def _check_scales(
    scales: dict[str, int],
    postings: dict[str, list[_Posting]],
    balances: list[_Balance],
) -> None:
    """Every currency that carries an amount must be two-decimal.

    Only the currencies actually used are checked: an unused VTSAX row in
    ``commodity`` is harmless, while a used one would be rendered a hundred
    times too small by :func:`~ledgerbox.money.decimal_str`.
    """
    used = sorted(
        {posting.currency for legs in postings.values() for posting in legs}
        | {row.commodity_id for row in balances}
    )
    for code in used:
        scale = scales.get(code)
        if scale is None:
            raise BeancountExportError(f"currency {code!r} is used but not declared in commodity")
        if scale != SUPPORTED_SCALE:
            raise BeancountExportError(
                f"currency {code!r} has scale {scale}; this exporter renders "
                f"{SUPPORTED_SCALE}-decimal amounts only"
            )


def _check_account_name(name: str) -> None:
    """Refuse a name beancount's grammar cannot express.

    ``repo.ensure_account`` never renames an account after creating it, on the
    grounds that "Chase Checking" becoming "Rent Account" is the user's business.
    That freedom reaches here: a renamed account still has to be a beancount
    account name, or the whole export becomes unloadable — and the resulting
    parser message points at a line rather than at the account that caused it.

    The two rules checked are the ones no beancount version accepts a violation
    of: the root must be one of the five known types, and no component may start
    with a lowercase letter or contain whitespace or a quote. Anything subtler
    (which unicode letters count as uppercase, say) is left to ``bean-check``,
    because a false refusal here would leave the user with no export at all.
    """
    parts = name.split(":")
    if len(parts) < 2 or parts[0] not in ACCOUNT_ROOTS:
        raise BeancountExportError(
            f"account name {name!r} does not start with one of {', '.join(ACCOUNT_ROOTS)} "
            f"followed by ':'; beancount cannot represent it"
        )
    for part in parts[1:]:
        if not part or part[0].islower() or any(character.isspace() for character in part):
            raise BeancountExportError(
                f"account name {name!r} has the component {part!r}; every component must be "
                f"non-empty, start with an upper-case letter or a digit, and contain no spaces"
            )
        if '"' in part or ";" in part:
            raise BeancountExportError(
                f"account name {name!r} has the component {part!r}; a quote or a semicolon "
                f"would end the directive early"
            )


def _open_directives(accounts: list[_Account], first_seen: dict[str, str]) -> list[str]:
    """One ``open`` per account, in the query's ``(name, id)`` order.

    Accounts nothing references get :data:`EPOCH_DATE`. A chart of accounts is
    part of the ledger even where it is empty, and dropping the unused ones
    would make the export depend on activity rather than on structure.
    """
    return [
        f"{first_seen.get(account.id, EPOCH_DATE)} open {account.name} {account.currency}"
        for account in accounts
    ]


def _pad_directives(pads: dict[str, str], by_id: dict[str, _Account]) -> list[str]:
    equity = by_id.get(OPENING_BALANCES_ACCOUNT_ID)
    if equity is None:
        return []
    return [
        f"{pads[account_id]} pad {by_id[account_id].name} {equity.name}"
        for account_id in sorted(pads, key=lambda key: (pads[key], by_id[key].name, key))
    ]


def _txn_directives(
    txns: list[_Txn],
    postings: dict[str, list[_Posting]],
    by_id: dict[str, _Account],
    columns: _Columns,
) -> list[str]:
    lines: list[str] = []
    for txn in txns:
        # `is_transfer` is not decoration: the cashflow aggregations exclude
        # flagged transactions, so an export that dropped the flag could not
        # reproduce the ledger's own income and expense figures. It is the
        # *effective* value (rule, or the person who overruled the rule) — see
        # _TXN_SQL. A beancount tag is the format's own way to say it and costs
        # one word.
        tag = f" #{TRANSFER_TAG}" if txn.is_transfer else ""
        lines.append(f"{txn.day} {txn.flag} {_description(txn)}{tag}")
        for posting in postings.get(txn.id, ()):
            account = by_id.get(posting.account_id)
            if account is None:  # pragma: no cover - foreign key makes this unreachable
                raise BeancountExportError(
                    f"posting {posting.txn_id}:{posting.seq} references unknown account "
                    f"{posting.account_id!r}"
                )
            lines.append("  " + columns.money(account.name, posting.amount_minor, posting.currency))
        lines.append("")
    return lines[:-1] if lines else lines


def _description(txn: _Txn) -> str:
    """``"narration"``, or ``"payee" "narration"`` once P2 extracts payees.

    An absent narration is written as an empty string rather than omitted, so
    every transaction line has the same shape and a grep for ``* "`` finds all
    of them. P0 never sets ``payee`` — see :mod:`ledgerbox.ledger.posting` on
    why a guessed merchant name is not written down as a fact.
    """
    narration = quote(txn.narration or "")
    return narration if txn.payee is None else f"{quote(txn.payee)} {narration}"


def _balance_directives(
    balances: list[_Balance], by_id: dict[str, _Account], columns: _Columns
) -> list[str]:
    return [
        f"{next_day(row.as_of)} balance "
        + columns.money(by_id[row.account_id].name, row.amount_minor, row.commodity_id)
        for row in balances
    ]


# ---------------------------------------------------------------------------
# writing
# ---------------------------------------------------------------------------


def export_beancount(conn: sqlite3.Connection, target: Path) -> Path:
    """Render the ledger and write it to *target*. Returns the file written.

    A *target* that is an existing directory gets :data:`EXPORT_FILENAME` inside
    it, so ``export_beancount(conn, paths.export)`` does the obvious thing;
    anything else is used as the file path verbatim.

    The write is atomic. An export interrupted halfway must leave the previous
    export intact — a truncated escape hatch is worse than a stale one, because
    it still looks like a file.
    """
    destination = Path(target)
    if destination.is_dir():
        destination = destination / EXPORT_FILENAME
    return atomic_write_text(destination, render_beancount(conn))
