# SPDX-License-Identifier: AGPL-3.0-or-later
"""The wire format. Every response body in this application is defined here.

Kept in one module on purpose. The API, the static frontend and the tests are
three independent readings of the same shapes, and the cheapest way for them to
disagree is for each to describe the payload in its own words. A route that
returns one of these models cannot quietly grow a field the page never renders,
and the OpenAPI document FastAPI generates from them is the frontend's
reference rather than a second description someone has to keep in step.

**All money is integer minor units, and every such field says so in its name.**
The suffix is load-bearing: it is what tells a reader that ``-21240`` is
−$212.40 and not −$18,420, and it is why the frontend can format amounts in one
place instead of guessing per field. ``docs/STATUS.md`` §9.1 — no floats reach
this layer, and none leave it.

Human-readable strings (``message``, ``summary``) are for display only. Nothing
should ever parse them; the structured sibling is always right there.
"""

from __future__ import annotations

from typing import Any, Literal, Self

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from ..db.repo import MAX_PAGE_SIZE

__all__ = [
    "AnalyticsOut",
    "AgentCenterClientOut",
    "AgentCenterLedgerOut",
    "AgentCenterOut",
    "AgentCenterPolicyIn",
    "AgentCenterPolicyOut",
    "CashflowMonthOut",
    "CategoryBreakdownOut",
    "CategoryOut",
    "CategoryPatch",
    "CategorySliceOut",
    "CheckOut",
    "DateSpanOut",
    "DeletionImpactOut",
    "DeletionPlanOut",
    "DeletionResultOut",
    "HealthOut",
    "MonthlyCashflowOut",
    "ResolveRequest",
    "ReviewItemOut",
    "ReviewListOut",
    "StatementOut",
    "TotalsOut",
    "TransactionListOut",
    "TransactionOut",
    "TransactionTotalsOut",
    "TransactionUpdateOut",
    "UploadResult",
]

#: Mirrors :mod:`ledgerbox.ingest.pipeline`'s outcome constants. Spelled out as
#: a Literal so an unknown status is a validation error here rather than an
#: unstyled badge in the browser.
UploadStatus = Literal["imported", "duplicate", "needs_review", "failed"]
Severity = Literal["block", "warn"]
CheckStatus = Literal["pass", "fail", "skip"]
ReviewStatus = Literal["open", "resolved", "dismissed"]
CategoryKind = Literal["income", "expense", "transfer"]

#: Which source produced the effective answer, per migrations 0006 and 0005.
#:
#: The category has a third value the transfer flag does not, and the asymmetry
#: is the point: ``txn.is_transfer`` is NOT NULL, so the rules always have an
#: answer, while ``posting.category_id`` is nullable and most of a real ledger
#: is null. Reporting "no rule claimed this" as ``rule`` would be a field
#: claiming a decision nobody made.
CategoryDecidedBy = Literal["rule", "override", "agent", "learned", "none"]
TransferDecidedBy = Literal["rule", "override", "agent", "learned"]

#: Sortable columns. Mirrors :data:`ledgerbox.db.repo.SORT_KEYS`, which is the
#: whitelist the SQL is interpolated from; spelled as a Literal here so an
#: unknown key is a 422 from FastAPI rather than a ValueError from the query.
TransactionSort = Literal["date", "amount", "description", "category", "month"]

#: Sign filter for the transaction list. ``in`` is a deposit, ``out`` a
#: withdrawal — described by what they did to the balance, because on the bank
#: leg that is all they are known to have done.
TransactionDirection = Literal["in", "out"]


class CheckOut(BaseModel):
    """One reconciliation assertion's outcome.

    ``status`` distinguishes ``skip`` from ``pass`` because a block-level check
    that could not run has not established anything — the distinction the
    ``UNVERIFIED`` verdict exists to preserve (``docs/STATUS.md`` §5.8).
    """

    check_id: str
    severity: Severity
    status: CheckStatus
    message: str
    detail: dict[str, Any] = Field(default_factory=dict)


class ReviewItemOut(BaseModel):
    """One row of the review queue.

    ``detail`` is the parsed object, not the JSON string the database stores.
    The column holds ``{"message": ..., "detail": {...}}``; this model splits
    those two apart so the page never has to parse anything.
    """

    id: str
    source_file_id: str
    status: ReviewStatus
    severity: Severity
    check_id: str
    message: str
    detail: dict[str, Any] = Field(default_factory=dict)
    created_at: str | None = None
    resolved_at: str | None = None
    #: NULL when the file was refused before its period could be read — an
    #: unknown layout has no month.
    statement_month: str | None = None


class TotalsOut(BaseModel):
    """What was earned, what was spent, and what is left.

    Measured on the income and expense legs, never on the bank leg — see
    :func:`ledgerbox.db.repo.ledger_totals`. ``outflow_minor`` is negative, so
    ``inflow_minor + outflow_minor == net_minor``.

    ``transfer_count`` says how many transactions were flagged as transfers.
    The two ``transfer_excluded_*_minor`` fields say what that flagging cost
    the figures above, and they exist because **a count cannot be checked
    against anything and an amount can**.

    Flagging a line as a transfer takes money out of the headline: without the
    flag, ``inflow_minor`` would be larger by ``transfer_excluded_in_minor``
    and ``outflow_minor`` by ``transfer_excluded_out_minor``. One wrong flag
    therefore makes spending smaller and saving better, silently — which is the
    precise failure this project was built against. The predecessor counted
    77.5% of its "expenses" and 82.6% of its "income" from transfers and
    reported a 78% savings rate against a true rate near zero. "3 transfer(s)"
    tells nobody whether that is happening; "$4,200.00 taken out of Out" lets a
    person compare it against a statement and find out.

    Neither field is a verdict. An exclusion may be entirely correct, and these
    numbers make no claim either way — they are what makes the incorrect one
    visible.

    Measured on the same legs and carrying the same sign convention as the
    figures they were removed from: in is ``>= 0``, out is ``<= 0``, and both
    are 0 when nothing is flagged. They default to 0 so this contract can be
    read — and rendered — before the query that fills them exists.

    **``balance_minor`` is ``null`` when this window selects no posting of an
    account you own**, and a client must not render that as $0.00. The other
    figures are sums over a set the caller chose and are truthfully zero when
    that set is empty — nothing came in, because nothing is here. A balance is
    not a sum over the window, it is a position at the end of it, and a window
    closing before this ledger begins asks about a day the ledger has no
    evidence for. Printing $0.00 there states that somebody's account held
    nothing on a date nothing was ever recorded about, which is the same
    over-reach ``/api/health`` avoids by sending ``totals: null`` on an empty
    ledger rather than a zeroed one.
    """

    inflow_minor: int
    outflow_minor: int
    net_minor: int
    #: ``null`` when nothing in this window bears on the balance. Never zero as
    #: a stand-in for that: zero is a balance that was measured.
    balance_minor: int | None
    txn_count: int
    transfer_count: int
    #: Inflow the transfer flags kept out of ``inflow_minor``. ``>= 0``.
    transfer_excluded_in_minor: int = 0
    #: Outflow the transfer flags kept out of ``outflow_minor``. ``<= 0``.
    transfer_excluded_out_minor: int = 0


class StatementOut(BaseModel):
    """One ingested statement, with its queue state."""

    source_file_id: str
    institution: str | None
    period_start: str | None
    period_end: str | None
    statement_month: str | None
    byte_len: int
    ingested_at: str
    txn_count: int
    open_block: int
    open_warn: int


class CategoryOut(BaseModel):
    """One category a person can choose from.

    The mirror of the shipped rules file, not a second list kept for the UI.
    ``kind`` is what the page groups by — and the one value that changes a
    number is ``transfer``, which does so through ``v_txn_transfer`` and not
    because anything sums categories.
    """

    id: str
    kind: CategoryKind
    parent_id: str | None = None


class TransactionOut(BaseModel):
    """One statement line, rendered single-entry: the bank leg.

    ``category_id`` and ``is_transfer`` are the **effective** answers — what a
    person or Agent decided, folded over what the rules derived (migrations
    0005, 0006 and 0011). The two ``*_decided_by`` fields say which source spoke, and they are
    not decoration: they are the only way to tell a rule that fired from a rule
    that fired and was overruled, and the only way to tell "nothing claimed this
    line" from "somebody chose this".

    ``category_id`` is ``null`` for a line no rule claimed — 285 of the 415 real
    ones. There is no "other" category to fall into (``docs/STATUS.md`` §5.38),
    and a client that renders ``null`` as a named bucket has reintroduced the
    predecessor's best-looking bug: a catch-all is indistinguishable in a chart
    from a category something matched on purpose.

    ``raw_descriptor`` keeps the database's name because it keeps the database's
    promise: it is the bank's line **verbatim**, never normalised in place.
    """

    txn_id: str
    posting_id: str
    date: str
    #: NULL only if the identity row has no statement behind it — a condition
    #: ``verify``'s ``provenance`` check exists to report.
    statement_month: str | None = None
    amount_minor: int
    currency: str
    raw_descriptor: str
    occurrence_index: int
    category_id: str | None = None
    category_decided_by: CategoryDecidedBy
    is_transfer: bool
    transfer_decided_by: TransferDecidedBy
    source_file_id: str | None = None


class TransactionTotalsOut(BaseModel):
    """What the matched lines did to this account's balance.

    **Not income and spending, and deliberately not named as if they were.**
    These sum the **bank leg** — every matched line, transfers included — while
    :class:`TotalsOut` sums the income and expense legs, drops anything flagged
    as a transfer and never sees the opening entry. Different questions; the
    ``bank_`` prefix is here so that no reader has to have been told.

    **They line up exactly while this list holds the lines those figures
    count**, and that is the whole of it. Not "they will not agree" — this
    docstring said that, and it is published as the OpenAPI description, so a
    refuted sentence was being served to every client. Two acceptance rounds
    were needed to get the claim right: the first found the two responses
    identical to the cent out of the box, and the second found that "any filter
    separates them" is false too — ``transfer=false`` on a ledger with nothing
    marked selects every row, and so does a search for a single space. The
    condition is about *which rows*, never about whether a filter was typed.

    Scope, because a version of this without one is what needed correcting
    twice: it holds while :func:`ledgerbox.ledger.posting.build_entries` is the
    only producer of these rows — the same scope limit
    :func:`ledgerbox.db.repo.ledger_totals` states for the guarantee it rests
    on. ``tests/test_api.py`` pins the six cases rather than leaving this
    paragraph to be the check, which is what ``docs/STATUS.md`` §5.45 concluded
    the last time a sentence in this family kept being refuted.

    ``matched`` describes the whole filter, not the page. It is the number the
    pager reads, and it comes out of the same statement as the sums beside it,
    so those can never describe different sets of rows.
    """

    matched: int
    #: Sum of the matched lines that increased the balance. ``>= 0``.
    bank_in_minor: int
    #: Sum of the matched lines that decreased it. ``<= 0``.
    bank_out_minor: int
    bank_net_minor: int


class LargeFlowsOut(BaseModel):
    """Large lines no person has directly confirmed, biggest money first."""

    threshold_minor: int
    items: list[TransactionOut] = Field(default_factory=list)
    total_count: int
    truncated: bool


class TransactionListOut(BaseModel):
    """One page of transactions, plus what the whole filter matched.

    ``items`` is the slice; ``totals`` describes everything the filter selected.
    Both are read inside one transaction on one connection, so the page and its
    own summary cannot come from two different states of the ledger.
    """

    items: list[TransactionOut] = Field(default_factory=list)
    totals: TransactionTotalsOut
    limit: int
    offset: int
    sort: TransactionSort
    descending: bool
    #: One sentence for the top of the table. Display only.
    summary: str


class CategoryPatch(BaseModel):
    """A person's decision about one transaction's category.

    One field, and it is **required even when null**: ``null`` means "withdraw
    my decision and let the rules answer again", which is a real instruction and
    must not be what an empty body does by accident.

    There is no ``is_transfer`` field and there will not be one. A transfer is
    said by naming the category whose kind is ``transfer``, and "it is not a
    transfer" by naming any income or expense category — one table meaning one
    sentence, both directions reachable, no sentinel (``docs/STATUS.md`` §5.49).
    A second field here would be a second definition of what counts as a
    transfer, which §5.29 is the standing record of the cost of.
    """

    category_id: str | None


class TransactionUpdateOut(BaseModel):
    """What one PATCH did, and the row as it now reads.

    ``changed`` is false when the stored decision already named this category.
    It is not decoration: it is the only evidence the caller has that the click
    landed, the same reason the repository layer returns a count of rows changed
    rather than None (``docs/STATUS.md`` §5.44).

    ``transaction`` is re-read after the write, on the same connection, rather
    than assembled from what was sent. A response echoing the request would
    agree with itself no matter what the database did.
    """

    changed: bool
    transaction: TransactionOut
    #: One sentence for the row's status line. Display only.
    summary: str


#: The most transactions one bulk decision may name — imported rather than
#: written down again, because it has to be **the same number** as the page size
#: and a copy is the one that goes stale (§5.29). The page builds its selection
#: out of one read, so anything it can select in one request it can send in one;
#: a ceiling above that would let a client name rows it never saw.
MAX_BULK_TRANSACTIONS = MAX_PAGE_SIZE


class BulkCategoryPatch(BaseModel):
    """One decision, about a list of transactions the caller names.

    **The ids are explicit and there is no "apply to my filter" form.** A filter
    is a query, and the set it matches can change between the moment a person
    reads a count off the screen and the moment a write lands. A list is a set
    somebody saw, counted, and can be shown again; it cannot quietly grow. The
    cost is that the client has to fetch the ids, and that cost is the feature
    working the way it looks like it works.

    ``category_id`` means exactly what it means on
    :class:`CategoryPatch`, because it is passed to the same two repository
    functions: naming the ``transfer`` category says "these are transfers",
    naming any income or expense category says they are not, and ``null``
    withdraws the decisions and lets the rules answer again. There is no
    ``is_transfer`` field here for the same reason there is none there.

    This endpoint exists because the shipped rules claim **none** of the
    author's 415 real lines, and 86.9% of the unclaimed spending is money moving
    between the author's own accounts — 79 rows, one click each.
    """

    # This write shape is intentionally narrower than an ordinary permissive
    # response model.  In particular, silently ignoring a sibling ``filter``
    # would let an Agent believe the server evaluated selection semantics that
    # never participated in the write.
    model_config = ConfigDict(extra="forbid")

    txn_ids: list[str] = Field(min_length=1, max_length=MAX_BULK_TRANSACTIONS)
    category_id: str | None

    @field_validator("txn_ids")
    @classmethod
    def transaction_ids_are_unique(cls, value: list[str]) -> list[str]:
        """Keep response counts about transactions, not repeated list positions."""
        if len(value) != len(set(value)):
            raise ValueError("txn_ids must be unique")
        return value


class BulkCategoryOut(BaseModel):
    """What one bulk decision did, counted by kind.

    ``replaced`` is the field worth reading first. The others describe work;
    that one describes a **loss**: those lines already carried a category
    somebody chose by hand, and this replaced it. ``archive/`` holds documents,
    not what a person decided about them, so re-ingesting brings the
    transactions back and never the decision — which is why ``forget`` names its
    irreversible kinds on their own line rather than inside a total, and why
    this one is not folded into ``changed``.

    ``transfer_added`` and ``transfer_removed`` are the consequence that reaches
    past these rows: a line becoming a transfer leaves the In and Out at the top
    of the page, and one ceasing to be a transfer rejoins them. Counts, never
    amounts — the amounts belong to the query that owns those figures.

    There is no 409 and no confirmation step. An override can be withdrawn and
    the rules will answer again, so asking a person to confirm a reversible act
    would be ceremony with nothing behind it (``docs/STATUS.md`` §5.72). What
    ``replaced`` covers is not reversible by repeating this call, which is why
    it is reported rather than guarded: the client is expected to say how many
    decisions it is about to replace **before** the click, out of the
    ``category_decided_by`` it already has for every row on screen.
    """

    requested: int
    changed: int
    unchanged: int
    replaced: int
    transfer_added: int
    transfer_removed: int
    #: One sentence for the toolbar. Display only, and it names the loss.
    summary: str


class CashflowMonthOut(BaseModel):
    """One month of the monthly chart, keyed by the **transaction** date.

    The field is ``month`` and not ``statement_month`` because it is not one,
    and the name changed when the meaning did. The two differ for any line near
    a period boundary, since a Chase statement period does not begin on the 1st.

    * ``month`` here answers *when did this happen*. It is the axis a person
      reads, and the only one that can express a range like "the last week".
    * ``statement_month`` — still what ``/api/statements`` and the transaction
      table's month filter mean — answers *which statement is this printed on*,
      and is derived from the period's **end** day, because taking the start day
      is what made three months vanish from the predecessor's output.

    Both questions exist in this product and both are labelled wherever they
    appear. The predecessor had both and labelled neither: its chart bucketed
    one way, its table the other, and 83 of its 415 rows landed in different
    months with nothing on screen saying so.

    Never null — a transaction always has a date.
    """

    month: str
    #: Sum of the lines that increased the balance. ``>= 0``.
    inflow_minor: int
    #: Sum of the lines that decreased it. ``<= 0``, so ``in + out == net``.
    outflow_minor: int
    net_minor: int
    txn_count: int


class MonthlyCashflowOut(BaseModel):
    """Money in and out per transaction month, oldest first.

    These are :class:`TotalsOut`'s figures **decomposed by month**, measured on
    the same legs over the same rows — so the months sum back to the four
    figures for any date range, by construction rather than by two queries
    happening to agree. ``verify``'s ``cashflow_agreement`` asserts the unscoped
    case against a view built by an independent path.

    The sums come from the same rows as ``months``, so the figure under the
    chart cannot describe a different set of months than the bars above it.
    """

    months: list[CashflowMonthOut] = Field(default_factory=list)
    inflow_minor: int
    outflow_minor: int
    net_minor: int
    txn_count: int


class CategorySliceOut(BaseModel):
    """One category's share of what was spent.

    ``category_id`` is **null** when no rule claimed those lines and nobody has
    overruled that. It is a slice like any other and it must be drawn as one,
    with area and with a label saying nothing claimed it — never as "other".

    That is not a style preference. The predecessor's breakdown looked complete
    because its catch-all bucket was also a wrong rule, so the leftovers came to
    almost nothing and a chart with a real defect in it rendered perfectly. A
    bucket that collects what is left over is indistinguishable, in a chart,
    from one that was matched on purpose. There is no ``uncategorized`` category
    in this ledger to fall into, and this field is null rather than filled in
    for the same reason.

    ``spend_minor`` is **negative**, in the same sign convention as
    :attr:`TotalsOut.outflow_minor`.
    """

    category_id: str | None = None
    spend_minor: int
    txn_count: int


class CategoryBreakdownOut(BaseModel):
    """What each category cost, largest first, and what they add up to.

    ``total_minor`` is the sum of ``slices`` and is not queried separately, so
    no client can render a total its own wedges contradict.

    **It is also equal to** :attr:`TotalsOut.outflow_minor` — the Out already
    printed at the top of the page. That is what makes this a breakdown of a
    figure rather than a further measurement of the same money, and it is why
    ``v_category_spend`` reads the expense legs: the arrangement was chosen to
    make the equality hold, not observed afterwards.

    Being exact about what backs that, because this project has published a
    description of this shape that was false — twice, and the second time was
    this paragraph. ``verify``'s ``cashflow_agreement`` check asserts it on the
    operator's own ledger, against **both** expressions of the breakdown: the
    SQL view and the query this response is actually built from. Checking only
    the view was the earlier shape, and an acceptance round edited the other one
    and watched the wedges sum to a twelfth of ``outflow_minor`` with every
    check green. ``tests/test_pipeline.py`` carries the negative cases, one per
    expression and per direction.

    The check's reach is narrower here than for the monthly view: no *data* can
    pull these two apart, because both sum the same rows under the same
    predicate and differ only in how they group them. What is left is an edit —
    and only an edit **that changes what one of them sums to**. Pointing every
    wedge at one category id leaves the total alone and passes, which is a real
    limit and is stated here rather than discovered later;
    ``tests/test_analytics.py`` is what covers the grouping.
    ``ledgerbox.ingest.pipeline.cashflow_disagreements`` lists every comparison
    and its reach. It lists rather than counts them: the count was wrong in this
    docstring, in that one, and in two documents at once.

    ``txn_count`` counts the transactions behind the spending. It is **not**
    comparable with :attr:`TotalsOut.txn_count`, which counts income and expense
    together.
    """

    slices: list[CategorySliceOut] = Field(default_factory=list)
    total_minor: int
    txn_count: int


class DateSpanOut(BaseModel):
    """The window every figure in this response was measured over.

    Echoed back rather than assumed, so a client can tell what it actually got.
    ``null`` on either end means unbounded, and both ends are **inclusive**.

    These are transaction dates. That is the same question the page's range
    control asks and a different one from ``statement_month``, which asks which
    statement a line is printed on; see :class:`CashflowMonthOut`.
    """

    since: str | None = None
    until: str | None = None


class AnalyticsOut(BaseModel):
    """Both charts and the figures above them, from one read of the ledger.

    ``totals`` is here rather than only on ``/api/health`` because it has to
    move with the date range, and because the two charts are its two
    decompositions — by month and by category. All three come out of one read,
    so the headline and the pictures under it cannot describe different windows.

    ``/api/health``'s ``totals`` remains the **unscoped** ledger. A figure that
    can be narrowed and a figure that states what the ledger holds are different
    claims, and this is the one that can be narrowed.

    Nothing in the shipped page reads that unscoped copy any more — the status
    strip beside it reads ``integrity_ok``, ``open_block`` and the schema
    version, and the four figures come from here. It is kept because
    ``/api/health`` answers "is this ledger sound" to anything that asks,
    including a caller that is not this page, and that answer should not depend
    on a window. The next reader of it should know it currently has none.

    One response rather than the two endpoints ``docs/EXECUTION_PLAN.md`` §6
    sketched, and the reason is the one that put ``read_transaction`` under the
    transaction table: two requests are two snapshots, and two charts drawn
    side by side each claiming to describe *this ledger* should not be able to
    describe two different ones. Both queries are read inside a single deferred
    read transaction.

    Empty lists before anything is booked. The zero sums beside them are a
    measurement — nothing has been spent because nothing is here — unlike
    :attr:`HealthOut.totals`, which is null on an empty ledger because a balance
    of $0.00 would read as a fact about money rather than as an absence of any.
    """

    span: DateSpanOut
    #: ``None`` on a ledger with nothing booked, for the reason
    #: :attr:`HealthOut.totals` is: zero money is a claim, no rows is a fact.
    totals: TotalsOut | None = None
    monthly: MonthlyCashflowOut
    categories: CategoryBreakdownOut


class DeletionImpactOut(BaseModel):
    """How many rows a deletion takes out. Counted, never estimated.

    The two balance-assertion fields come from one query asking, for each
    assertion this statement owns, whether a *surviving* statement still prints
    the same balance on the same day. One that does inherits the row — the
    statements were compared when the second was ingested, and disagreeing would
    have raised rather than been written — so the row stays and its provenance
    moves. One that does not leaves nothing behind, and the row goes.

    ``category_overrides``, ``agent_proposals`` and ``review_items_decided`` are the fields to
    read twice. Every other row here is derived from bytes a re-ingest replays;
    those two are decisions a person made, and ``archive/`` holds documents
    rather than decisions. A re-ingest brings a dismissed queue item back as
    ``open``, never as dismissed (``docs/STATUS.md`` §5.49, §5.65).
    """

    txns: int
    postings: int
    txn_identities: int
    raw_records: int
    review_items: int
    #: Of ``review_items``, the ones somebody had already resolved or dismissed.
    #: Not recoverable — see this model's docstring.
    review_items_decided: int = 0
    category_overrides: int
    agent_proposals: int
    agent_proposal_runs: int
    agent_triage_items: int
    agent_triage_runs: int
    balance_assertions_removed: int
    #: Kept, with provenance moved to a statement that still prints the balance.
    balance_assertions_reassigned: int


class DeletionPlanOut(BaseModel):
    """What deleting one statement would do, measured before anything is written.

    ``checks_after`` is not a forecast in the usual sense. The deletion is
    performed inside a transaction, ``verify`` is run against the result, and the
    transaction is rolled back — so these are the checks' real answers, produced
    by the same code that will run for real.

    It carries **six** of the nine, and ``checks_note`` says which three are
    missing and why. The archive checks cannot be measured this way: the file is
    still on disk while the ledger rows are gone, which is a state that never
    exists once the deletion completes. Reporting them would be reporting a
    failure that is an artefact of how the measurement was taken.

    ``allowed`` false means this deletion will be refused whatever the caller
    sends next, and ``refusals`` says why in sentences meant to be read.
    """

    source_file_id: str
    statement_month: str | None
    period_start: str | None
    period_end: str | None
    allowed: bool
    refusals: list[str] = Field(default_factory=list)
    impact: DeletionImpactOut
    #: Empty when ``allowed`` is false: nothing was simulated, because nothing
    #: is going to happen.
    checks_after: list[CheckOut] = Field(default_factory=list)
    checks_note: str
    totals_before: TotalsOut | None = None
    totals_after: TotalsOut | None = None
    archive_file_present: bool
    extracted_file_present: bool
    #: One sentence for the confirmation prompt. Display only.
    summary: str


class DeletionResultOut(BaseModel):
    """What a completed deletion actually removed.

    ``checks_after`` is all **nine** here, archive included: by this point the
    disk and the database agree again, so there is nothing left that a full
    ``verify`` would misreport.

    ``unremoved_files`` is normally empty and is not an afterthought. The ledger
    rows are deleted first on purpose, so a file that could not be removed leaves
    bytes on disk with no row behind them. ``ledgerbox doctor`` reports both
    kinds — a stranded archived statement and a stranded extraction cache — and
    exits non-zero until they are gone. Saying which file, and why it could not
    be deleted, is the difference between that being a task and being a mystery.

    This paragraph named ``verify``'s ``archived_not_recorded`` until an
    acceptance run took the trouble to hold a handle on the *extraction cache*
    rather than the PDF: that check walks ``archive/`` and nothing else, so all
    nine checks came back green over a leftover ``.ndjson`` holding the whole
    text layer. The claim was true of the file the author had in mind and false
    of the other one.
    """

    source_file_id: str
    statement_month: str | None
    removed: DeletionImpactOut
    removed_files: list[str] = Field(default_factory=list)
    #: ``[[path, reason], …]``.
    unremoved_files: list[list[str]] = Field(default_factory=list)
    checks_after: list[CheckOut] = Field(default_factory=list)
    totals: TotalsOut | None = None
    summary: str


class UploadResult(BaseModel):
    """What one upload did.

    ``status`` is the only field worth branching on:

    ``imported``
        the statement passed every block-level check and is in the ledger.
    ``duplicate``
        these exact bytes were already archived and nothing was outstanding.
        No side effects at all — that is what content addressing buys.
    ``needs_review``
        archived and queued, **nothing booked**. ``review`` says why.
    ``failed``
        not even archivable (not a PDF, unreadable, too large). No row exists,
        so there is nothing to queue; ``error`` is the whole story.
    """

    status: UploadStatus
    filename: str
    sha256: str | None = None
    statement_month: str | None = None
    #: ``ok`` / ``BLOCKED`` / ``UNVERIFIED (n block-level check(s) could not run)``
    verdict: str | None = None
    booked: int = 0
    skipped_duplicates: int = 0
    #: One sentence for the top of the panel. Display only.
    summary: str
    checks: list[CheckOut] = Field(default_factory=list)
    review: list[ReviewItemOut] = Field(default_factory=list)
    error: str | None = None


class ReviewListOut(BaseModel):
    items: list[ReviewItemOut]
    open_block: int
    open_warn: int


class ResolveRequest(BaseModel):
    """A human's decision about one queued item. It never books anything.

    This is the gate's most tempting hole and it stays shut: "resolve" records
    that a person looked, and that is all it does. The only way a refused
    statement enters the ledger is to fix the parser and re-ingest the same
    archived bytes — which is possible precisely because the bytes were kept.

    ``acknowledge_unbooked`` is required to dismiss a **block**-level item.
    Dismissing one means accepting a statement that was never booked, and
    ``verify``'s ``unbooked_statements`` check keeps saying so afterwards; the
    flag exists so that acceptance is typed out rather than clicked past.
    """

    action: Literal["resolve", "dismiss"]
    acknowledge_unbooked: bool = False
    note: str | None = None


class HealthOut(BaseModel):
    """Version, paths and queue depth. ``docs/EXECUTION_PLAN.md`` §6."""

    version: str
    schema_version: int
    schema_latest: int
    data_dir: str
    database: str
    database_present: bool
    integrity_ok: bool
    rows: dict[str, int] = Field(default_factory=dict)
    open_block: int = 0
    open_warn: int = 0
    statement_months: int = 0
    totals: TotalsOut | None = None


# A1 proposal audit.  The write models all forbid unknown fields: an Agent
# receiving 2xx for a sibling `filter` or `confidence` the service ignored is a
# more dangerous contract than an explicit 422.
HASH_ID_PATTERN = r"^sha256:[0-9a-f]{64}$"


class AgentProducerIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    client: Literal["codex", "claude-code", "other"]
    client_version: str | None = Field(default=None, max_length=200)
    model_reported: str | None = Field(default=None, max_length=200)


class AgentCenterPolicyOut(BaseModel):
    selected_client: Literal["codex", "claude-code"] | None
    application_mode: Literal["review_first", "automatic"]
    enabled: bool
    auto_classify_new_imports: bool


class AgentCenterPolicyIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    selected_client: Literal["codex", "claude-code"] | None
    application_mode: Literal["review_first", "automatic"]
    enabled: bool = Field(strict=True)
    auto_classify_new_imports: bool = Field(strict=True)
    acknowledge_provider_data_policy: bool = Field(strict=True)


ClientOutcomeOut = Literal["exited", "timeout", "not_found", "spawn_failed", "workspace_missing"]


class AgentCenterLedgerOut(BaseModel):
    ready_for_proposals: bool
    passed_checks: int
    total_checks: int
    proposal_schema_version: Literal[2]
    uncategorized_count: int
    pending_review_count: int
    pending_triage_count: int
    open_review_count: int
    ledger_label: str
    data_dir: str


class AgentCenterClientOut(BaseModel):
    client: Literal["codex", "claude-code"]
    installed: bool
    runner_skill_compatible: bool
    personal_skill_state: Literal["missing", "current", "outdated", "custom"]
    mcp_bridge_available: bool
    mcp_session: Literal["active", "seen_before", "not_seen"]
    session_active: bool
    last_seen_at: str | None
    last_result: Literal["completed", "partial", "failed"] | None
    result_at: str | None
    candidate_count: int | None
    submitted_count: int | None
    error_code: str | None


class AgentClassificationJobOut(BaseModel):
    client: Literal["codex", "claude-code"]
    application_mode: Literal["review_first", "automatic"]
    state: Literal["queued", "running", "completed", "partial", "failed"]
    candidate_count: int | None
    submitted_count: int | None
    applied_count: int | None
    omitted_count: int | None
    error_code: str | None
    # Aggregate runner facts only. The client's log excerpt is never serialised
    # here: it quotes the operator's own bank descriptors and stays on disk.
    client_outcome: ClientOutcomeOut | None
    client_exit_code: int | None
    queued_at: str
    started_at: str | None
    finished_at: str | None


class AgentClassificationBatchOut(BaseModel):
    """One stretch of work, so a multi-round import is not read as its last round."""

    job_count: int
    state: Literal["queued", "running", "completed", "partial", "failed"]
    candidate_count: int | None
    submitted_count: int
    applied_count: int
    omitted_count: int | None
    error_code: str | None
    client_outcome: ClientOutcomeOut | None
    rounds_capped: bool
    failed_rounds: int
    max_rounds: int
    queued_at: str
    started_at: str | None
    finished_at: str | None


class AgentCenterOut(BaseModel):
    schema_version: Literal[3]
    ledgerbox: AgentCenterLedgerOut
    policy: AgentCenterPolicyOut
    clients: list[AgentCenterClientOut]
    latest_batch: AgentClassificationBatchOut | None
    latest_job: AgentClassificationJobOut | None
    provider_disclosure: str
    run_prompts: dict[Literal["codex", "claude-code"], str]
    setup_commands: dict[Literal["codex", "claude-code"], str]
    setup_guide: str


class AgentProposalGroupIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    group_id: str = Field(pattern=HASH_ID_PATTERN)
    category_id: str = Field(min_length=1, max_length=200)
    txn_ids: list[str] = Field(min_length=1, max_length=MAX_PAGE_SIZE)

    @field_validator("txn_ids")
    @classmethod
    def transaction_ids_are_unique(cls, value: list[str]) -> list[str]:
        if len(value) != len(set(value)):
            raise ValueError("txn_ids must be unique")
        return value


class AgentProposalSubmitIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    schema_version: Literal[1, 2]
    application_mode: Literal["review_first", "automatic"] | None = None
    ledger_revision: str = Field(pattern=HASH_ID_PATTERN)
    producer: AgentProducerIn
    groups: list[AgentProposalGroupIn] = Field(max_length=MAX_PAGE_SIZE)

    @model_validator(mode="after")
    def application_mode_matches_version(self) -> Self:
        if self.schema_version == 1 and "application_mode" in self.model_fields_set:
            raise ValueError("proposal schema version 1 is permanently review-only")
        if self.schema_version == 2 and self.application_mode is None:
            raise ValueError("proposal schema version 2 requires application_mode")
        if self.schema_version == 1 and not self.groups:
            # Frozen v1 semantics; the honest empty proposal is v2-only.
            raise ValueError("a schema v1 proposal run must contain at least one group")
        return self


class AgentProposalStatusOut(BaseModel):
    schema_version: Literal[2]
    ledger_revision: str


class AgentProposalOut(BaseModel):
    txn_id: str
    group_id: str
    suggested_category_id: str
    outcome: Literal["pending", "accepted", "edited", "rejected", "withdrawn"]
    applied_category_id: str | None
    reviewed_at: str | None
    current_transaction: TransactionOut | None


class AgentProposalRunSummaryOut(BaseModel):
    run_id: str
    created_at: str
    state: Literal["open", "completed", "dismissed"]
    producer: AgentProducerIn
    proposal_count: int
    pending: int
    accepted: int
    edited: int
    rejected: int
    withdrawn: int


class AgentProposalRunOut(BaseModel):
    run_id: str
    ledger_revision: str
    schema_version: Literal[1, 2]
    application_mode: Literal["review_first", "automatic"] | None
    producer: AgentProducerIn
    created_at: str
    state: Literal["open", "completed", "dismissed"]
    proposals: list[AgentProposalOut]


class AgentProposalSubmitOut(BaseModel):
    run_id: str
    created: bool
    proposal_count: int


class AgentProposalReviewIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    action: Literal["accept", "reject"]
    txn_ids: list[str] = Field(min_length=1, max_length=MAX_PAGE_SIZE)
    category_id: str | None = Field(default=None, min_length=1, max_length=200)

    @field_validator("txn_ids")
    @classmethod
    def transaction_ids_are_unique(cls, value: list[str]) -> list[str]:
        if len(value) != len(set(value)):
            raise ValueError("txn_ids must be unique")
        return value

    @model_validator(mode="after")
    def rejected_rows_have_no_applied_category(self) -> Self:
        if self.action == "reject" and self.category_id is not None:
            raise ValueError("a rejected proposal cannot carry category_id")
        return self


class AgentProposalReviewOut(BaseModel):
    run_id: str
    accepted: int
    edited: int
    rejected: int
    state: Literal["open", "completed", "dismissed"]


class AgentProposalWithdrawOut(BaseModel):
    run_id: str
    withdrawn: int
    already_absent: int
    changed_later: int
    # Withdrawal also takes the run's learned rules and the answers those rules
    # derived; changed lines beyond the run's own proposals are never silent.
    rules_unlearned: int
    learned_cleared: int


# A6.5 remaining-coverage triage.  Draft normalization belongs to the local
# Agent CLI/MCP boundary; HTTP accepts only the exact normalized submission and
# serves the local human review UI.
TriageRoute = Literal["possible_transfer", "taxonomy_gap", "uncertain"]
TriageOutcome = Literal[
    "pending",
    "confirmed_transfer",
    "confirmed_taxonomy_gap",
    "left_uncertain",
    "classified_existing",
    "stale",
    "withdrawn",
]


class AgentTriageScopeIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    since: str | None = Field(default=None, pattern=r"^\d{4}-\d{2}-\d{2}$")
    until: str | None = Field(default=None, pattern=r"^\d{4}-\d{2}-\d{2}$")

    @model_validator(mode="after")
    def dates_form_a_real_ordered_span(self) -> Self:
        from datetime import date

        for name in ("since", "until"):
            value = getattr(self, name)
            if value is not None:
                try:
                    date.fromisoformat(value)
                except ValueError as error:
                    raise ValueError(f"{name} is not a real date") from error
        if self.since is not None and self.until is not None and self.since > self.until:
            raise ValueError("since must not be after until")
        return self


class AgentTriageGroupIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    group_id: str = Field(pattern=HASH_ID_PATTERN)
    route: TriageRoute
    reason_code: str = Field(min_length=1, max_length=80)
    txn_ids: list[str] = Field(min_length=1, max_length=MAX_PAGE_SIZE)

    @field_validator("txn_ids")
    @classmethod
    def transaction_ids_are_unique(cls, value: list[str]) -> list[str]:
        if len(value) != len(set(value)):
            raise ValueError("txn_ids must be unique")
        return value


class AgentTriageSubmitIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    schema_version: Literal[1]
    ledger_revision: str = Field(pattern=HASH_ID_PATTERN)
    scope_revision: str = Field(pattern=HASH_ID_PATTERN)
    scope: AgentTriageScopeIn
    producer: AgentProducerIn
    groups: list[AgentTriageGroupIn] = Field(min_length=1, max_length=MAX_PAGE_SIZE)


class AgentTriageSubmitOut(BaseModel):
    run_id: str
    created: bool
    item_count: int


class AgentTriageItemOut(BaseModel):
    txn_id: str
    group_id: str
    route: TriageRoute
    reason_code: str
    outcome: TriageOutcome
    applied_category_id: str | None
    reviewed_at: str | None
    current_transaction: TransactionOut | None


class AgentTriageRouteSummaryOut(BaseModel):
    route: TriageRoute
    item_count: int
    pending: int
    bank_amount_minor: int


class AgentTriageRunSummaryOut(BaseModel):
    run_id: str
    created_at: str
    state: Literal["open", "completed", "dismissed"]
    scope: AgentTriageScopeIn
    producer: AgentProducerIn
    item_count: int
    pending: int
    confirmed_transfer: int
    confirmed_taxonomy_gap: int
    left_uncertain: int
    classified_existing: int
    stale: int
    withdrawn: int


class AgentTriageRunOut(BaseModel):
    run_id: str
    ledger_revision: str
    scope_revision: str
    schema_version: Literal[1]
    scope: AgentTriageScopeIn
    producer: AgentProducerIn
    created_at: str
    state: Literal["open", "completed", "dismissed"]
    route_summaries: list[AgentTriageRouteSummaryOut]
    items: list[AgentTriageItemOut]


class AgentTriageReviewIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    action: Literal["classify", "confirm_gap", "leave_uncertain"]
    txn_ids: list[str] = Field(min_length=1, max_length=MAX_PAGE_SIZE)
    category_id: str | None = Field(default=None, min_length=1, max_length=200)

    @field_validator("txn_ids")
    @classmethod
    def transaction_ids_are_unique(cls, value: list[str]) -> list[str]:
        if len(value) != len(set(value)):
            raise ValueError("txn_ids must be unique")
        return value

    @model_validator(mode="after")
    def category_matches_action(self) -> Self:
        if self.action == "classify" and self.category_id is None:
            raise ValueError("classify requires category_id")
        if self.action != "classify" and self.category_id is not None:
            raise ValueError(f"{self.action} cannot carry category_id")
        return self


class AgentTriageReviewOut(BaseModel):
    run_id: str
    confirmed_transfer: int
    confirmed_taxonomy_gap: int
    left_uncertain: int
    classified_existing: int
    state: Literal["open", "completed", "dismissed"]


class AgentTriageWithdrawOut(BaseModel):
    run_id: str
    withdrawn: int
    already_absent: int
    changed_later: int


class AgentTriageWithdrawSelectedIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    txn_ids: list[str] = Field(min_length=1, max_length=MAX_PAGE_SIZE)

    @field_validator("txn_ids")
    @classmethod
    def transaction_ids_are_unique(cls, value: list[str]) -> list[str]:
        if len(value) != len(set(value)):
            raise ValueError("txn_ids must be unique")
        return value
