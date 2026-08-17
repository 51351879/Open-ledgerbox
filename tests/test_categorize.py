# SPDX-License-Identifier: AGPL-3.0-or-later
"""The category engine, and the four predecessor defects it is shaped against.

Every loader rule below has a case that passes it and a case that trips it.
A refusal nobody has watched fire is a refusal that may not work: the guard in
``tests/test_repo_hygiene.py`` once exempted itself through a marker string
appearing twice, and reading the code did not show it.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from ledgerbox.analytics.categorize import (
    CANARIES,
    MIN_WORD_LENGTH,
    RULES_PATH,
    TRANSFER_CATEGORY_ID,
    RulesError,
    assign_categories,
    classify,
    default_rules,
    load_rules,
    matches_transfer,
    side_for,
)
from ledgerbox.ledger.posting import (
    EXPENSE_ACCOUNT_ID,
    INCOME_ACCOUNT_ID,
    counter_account_for,
)

# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------


def write_rules(directory: Path, payload: dict[str, Any]) -> Path:
    target = directory / "categories.json"
    target.write_text(json.dumps(payload), encoding="utf-8")
    return target


def one_category(**overrides: Any) -> dict[str, Any]:
    category: dict[str, Any] = {
        "id": "dining",
        "kind": "expense",
        "priority": 10,
        "rules": [{"type": "word", "patterns": ["chipotle"]}],
    }
    category.update(overrides)
    return {"version": 1, "categories": [category]}


# ---------------------------------------------------------------------------
# the shipped rules file
# ---------------------------------------------------------------------------


def test_the_shipped_rules_file_loads() -> None:
    rules = default_rules()
    assert rules.version == 1
    assert len(rules.categories) > 0


@pytest.mark.parametrize(
    ("descriptor", "expected"),
    [
        ("Recurring Card Purchase Crunch Fitness", "sport"),
        ("Card Purchase Nintendo eShop", "entertainment"),
        ("PayPal Instant Transfer Steam Games", "entertainment"),
    ],
)
def test_shipped_rules_cover_explicit_sport_and_entertainment_decisions(
    descriptor: str, expected: str
) -> None:
    assert classify(descriptor, -1000) == expected


def test_shipped_categories_are_sorted_by_kind_then_priority() -> None:
    """`classify` takes the first match, so the order is the meaning."""
    rules = default_rules()
    keys = [(rule.kind, rule.priority) for rule in rules.categories]
    assert keys == sorted(keys)


def test_shipped_rules_carry_no_long_digit_runs() -> None:
    """A category keyword must never be somebody's account or card number.

    The predecessor wrote a real card's last four digits into its category
    rules, which put identifying data inside logic where deleting the data
    could not remove it. This is a shape check, not a proof — the repository
    hygiene suite is the other half.
    """
    for rule in default_rules().categories:
        for source in rule.sources:
            digits = "".join(character for character in source if character.isdigit())
            assert len(digits) < 4, f"{rule.id}: {source!r} carries a digit run"


def test_shipped_rules_have_no_catch_all() -> None:
    for rule in default_rules().categories:
        for pattern in rule.patterns:
            for canary in CANARIES:
                assert pattern.search(canary) is None, f"{rule.id} matches {canary!r}"


# ---------------------------------------------------------------------------
# the two defects that made the predecessor's chart look complete
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "descriptor",
    [
        "Card Purchase        Some Shop",
        "CARD PURCHASE WITH PIN",
        "Recurring Card Purchase",
    ],
)
def test_purchase_does_not_contain_a_bank_fee(descriptor: str) -> None:
    """``"chase"`` inside ``"Purchase"`` cost the predecessor 68 rows and $11,726.

    Every Chase line says ``Card Purchase``, so a substring match on the bank's
    own name swallowed most of the statement into "bank fees" — and because it
    was also the silent fallback, "other" came to $33.78 and the breakdown
    looked immaculate.
    """
    assert classify(descriptor, -1234) != "fees"


def test_the_boundary_rule_still_matches_the_text_it_is_for(tmp_path: Path) -> None:
    """The negative case above must not be bought with a rule that never fires.

    ``chase`` has to keep matching where it really is a word, or the test above
    passes for the wrong reason — a pattern that matches nothing would satisfy
    it just as well.
    """
    target = write_rules(tmp_path, one_category(rules=[{"type": "word", "patterns": ["chase"]}]))
    rules = load_rules(target)
    assert classify("payment to chase card ending", -500, rules=rules) == "dining"
    assert classify("card purchase somewhere", -500, rules=rules) is None


def test_a_two_character_keyword_cannot_be_written(tmp_path: Path) -> None:
    """The predecessor's bare ``"76"`` matched any two adjacent digits.

    Sixteen ACH and Zelle rows were pulled into "transport" by it. This is
    refused at load rather than reviewed at merge.
    """
    target = write_rules(tmp_path, one_category(rules=[{"type": "word", "patterns": ["76"]}]))
    with pytest.raises(RulesError, match="shorter than"):
        load_rules(target)


def test_a_keyword_of_the_minimum_length_is_accepted(tmp_path: Path) -> None:
    word = "a" * MIN_WORD_LENGTH
    target = write_rules(tmp_path, one_category(rules=[{"type": "word", "patterns": [word]}]))
    assert load_rules(target).categories[0].sources == (word,)


def test_a_digit_keyword_that_is_long_enough_still_matches_on_a_boundary(
    tmp_path: Path,
) -> None:
    """Length alone is not the whole fix; the boundary is the other half."""
    target = write_rules(tmp_path, one_category(rules=[{"type": "word", "patterns": ["760"]}]))
    rules = load_rules(target)
    assert classify("store 760 main st", -100, rules=rules) == "dining"
    assert classify("ach 17605 transfer", -100, rules=rules) is None


# ---------------------------------------------------------------------------
# priority is declared, not the accident of file order
# ---------------------------------------------------------------------------


def test_lower_priority_number_wins(tmp_path: Path) -> None:
    target = write_rules(
        tmp_path,
        {
            "version": 1,
            "categories": [
                {
                    "id": "late",
                    "kind": "expense",
                    "priority": 90,
                    "rules": [{"type": "word", "patterns": ["market"]}],
                },
                {
                    "id": "early",
                    "kind": "expense",
                    "priority": 10,
                    "rules": [{"type": "word", "patterns": ["super market"]}],
                },
            ],
        },
    )
    rules = load_rules(target)
    assert classify("super market downtown", -100, rules=rules) == "early"
    assert classify("stock market fee", -100, rules=rules) == "late"


def test_two_categories_of_one_kind_cannot_share_a_priority(tmp_path: Path) -> None:
    target = write_rules(
        tmp_path,
        {
            "version": 1,
            "categories": [
                {
                    "id": "one",
                    "kind": "expense",
                    "priority": 10,
                    "rules": [{"type": "word", "patterns": ["aaa"]}],
                },
                {
                    "id": "two",
                    "kind": "expense",
                    "priority": 10,
                    "rules": [{"type": "word", "patterns": ["bbb"]}],
                },
            ],
        },
    )
    with pytest.raises(RulesError, match="priority 10 used twice"):
        load_rules(target)


def test_income_and_expense_may_share_a_priority(tmp_path: Path) -> None:
    """They never compete: sign has already chosen the side."""
    target = write_rules(
        tmp_path,
        {
            "version": 1,
            "categories": [
                {
                    "id": "spend",
                    "kind": "expense",
                    "priority": 10,
                    "rules": [{"type": "word", "patterns": ["aaa"]}],
                },
                {
                    "id": "earn",
                    "kind": "income",
                    "priority": 10,
                    "rules": [{"type": "word", "patterns": ["bbb"]}],
                },
            ],
        },
    )
    assert len(load_rules(target).categories) == 2


# ---------------------------------------------------------------------------
# no catch-all
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("pattern", [".", ".*", "[a-z]?", r"\d+", r"\w*"])
def test_a_pattern_broad_enough_to_be_a_fallback_is_refused(
    tmp_path: Path, pattern: str
) -> None:
    """This is the shape that made a wrong breakdown look complete.

    ``match=`` is on the canary wording specifically. Asserting only
    ``RulesError`` would pass for a pattern refused by some *other* rule, which
    is what an empty pattern string does -- it never reaches this check at all.
    """
    target = write_rules(
        tmp_path, one_category(rules=[{"type": "regex", "pattern": pattern}])
    )
    with pytest.raises(RulesError, match="becomes the silent fallback"):
        load_rules(target)


def test_an_empty_regex_is_refused_before_the_canaries_ever_run(tmp_path: Path) -> None:
    """Split out because it is a different refusal wearing the same exception."""
    target = write_rules(tmp_path, one_category(rules=[{"type": "regex", "pattern": ""}]))
    with pytest.raises(RulesError, match="needs a non-empty 'pattern'"):
        load_rules(target)


def test_an_unmatched_descriptor_is_none_rather_than_a_bucket() -> None:
    assert classify("zqxjvk wombat consortium", -4200) is None


# ---------------------------------------------------------------------------
# sign gating
# ---------------------------------------------------------------------------


def test_side_for_agrees_with_the_posting_layer() -> None:
    """One sign rule, read from where it is defined rather than restated."""
    for amount in (-10_000, -1, 0, 1, 10_000):
        expected = {
            INCOME_ACCOUNT_ID: "income",
            EXPENSE_ACCOUNT_ID: "expense",
        }[counter_account_for(amount)]
        assert side_for(amount) == expected


def test_an_expense_rule_cannot_claim_a_deposit(tmp_path: Path) -> None:
    """A refund from a grocery shop is income, not negative groceries."""
    target = write_rules(
        tmp_path,
        {
            "version": 1,
            "categories": [
                {
                    "id": "groceries",
                    "kind": "expense",
                    "priority": 10,
                    "rules": [{"type": "word", "patterns": ["fresh mart"]}],
                },
                {
                    "id": "refund",
                    "kind": "income",
                    "priority": 10,
                    "rules": [{"type": "word", "patterns": ["refund"]}],
                },
            ],
        },
    )
    rules = load_rules(target)
    assert classify("fresh mart purchase", -2500, rules=rules) == "groceries"
    assert classify("fresh mart refund", 2500, rules=rules) == "refund"
    assert classify("fresh mart credit", 2500, rules=rules) is None


# ---------------------------------------------------------------------------
# loader validation, one refusal at a time
# ---------------------------------------------------------------------------


def test_a_non_transfer_category_must_declare_patterns(tmp_path: Path) -> None:
    target = write_rules(tmp_path, one_category(rules=[]))
    with pytest.raises(RulesError, match="declares no patterns"):
        load_rules(target)


@pytest.mark.parametrize(
    ("payload", "message"),
    [
        ({"categories": []}, "version"),
        ({"version": 1}, "categories"),
        ({"version": 1, "categories": []}, "categories"),
        ({"version": "1", "categories": [{}]}, "version"),
    ],
)
def test_a_malformed_file_is_refused(
    tmp_path: Path, payload: dict[str, Any], message: str
) -> None:
    target = write_rules(tmp_path, payload)
    with pytest.raises(RulesError, match=message):
        load_rules(target)


def test_a_display_name_cannot_be_used_as_an_id(tmp_path: Path) -> None:
    """The predecessor put both languages in the key and split('/') them apart."""
    target = write_rules(tmp_path, one_category(id="Dining / 餐饮"))
    with pytest.raises(RulesError, match="stable key"):
        load_rules(target)


def test_an_unknown_kind_is_refused(tmp_path: Path) -> None:
    target = write_rules(tmp_path, one_category(kind="asset"))
    with pytest.raises(RulesError, match="not one of"):
        load_rules(target)


def test_an_unknown_rule_type_is_refused(tmp_path: Path) -> None:
    target = write_rules(tmp_path, one_category(rules=[{"type": "glob", "patterns": ["abc"]}]))
    with pytest.raises(RulesError, match="must be 'word' or 'regex'"):
        load_rules(target)


def test_a_regex_that_does_not_compile_is_refused(tmp_path: Path) -> None:
    target = write_rules(tmp_path, one_category(rules=[{"type": "regex", "pattern": "([a-z"}]))
    with pytest.raises(RulesError, match="does not compile"):
        load_rules(target)


def test_a_working_regex_rule_is_accepted(tmp_path: Path) -> None:
    target = write_rules(
        tmp_path, one_category(rules=[{"type": "regex", "pattern": r"\bsushi\b"}])
    )
    rules = load_rules(target)
    assert classify("HOUSE OF SUSHI", -1000, rules=rules) == "dining"


def test_a_file_that_is_not_json_is_refused(tmp_path: Path) -> None:
    target = tmp_path / "categories.json"
    target.write_text("{not json", encoding="utf-8")
    with pytest.raises(RulesError, match="not valid JSON"):
        load_rules(target)


def test_a_pattern_its_neighbour_already_matches_is_refused(tmp_path: Path) -> None:
    """``"service fee"`` matches everywhere ``"monthly service fee"`` does.

    Within one category the patterns are OR'd, so the longer one changes no
    outcome. Two patterns first written into the shipped file were exactly this
    and were only noticed by counting hits against a real corpus; the loader is
    what makes noticing unnecessary.
    """
    target = write_rules(
        tmp_path,
        one_category(
            rules=[{"type": "word", "patterns": ["service fee", "monthly service fee"]}]
        ),
    )
    with pytest.raises(RulesError, match="can never change an outcome"):
        load_rules(target)


def test_a_regex_is_never_accused_of_being_dead_on_its_own_source_text(
    tmp_path: Path,
) -> None:
    """The subsumption test compares text, and a regex's source is not a sample.

    ``"abc"`` matches the *source string* ``[0-9]abc[0-9]`` while matching none
    of the text that regex matches, so accusing the regex would delete a live
    rule. The first version of this check did exactly that; it was never
    reachable from the shipped file, which is precisely why it needed a test.
    """
    target = write_rules(
        tmp_path,
        one_category(
            rules=[
                {"type": "word", "patterns": ["abc"]},
                {"type": "regex", "pattern": "[0-9]abc[0-9]"},
            ]
        ),
    )
    rules = load_rules(target)
    assert classify("1abc2 something", -100, rules=rules) == "dining"
    assert classify("plain abc here", -100, rules=rules) == "dining"


def test_the_same_pattern_written_twice_is_refused(tmp_path: Path) -> None:
    target = write_rules(
        tmp_path, one_category(rules=[{"type": "word", "patterns": ["chipotle", "chipotle"]}])
    )
    with pytest.raises(RulesError, match="twice"):
        load_rules(target)


def test_overlap_between_categories_is_allowed_because_priority_decides(
    tmp_path: Path,
) -> None:
    """The check is within a category. Across them, overlap *is* the feature.

    Dining claiming ``uber eats`` before transport sees ``uber`` is the whole
    reason ``priority`` exists, so the loader must not refuse it.
    """
    target = write_rules(
        tmp_path,
        {
            "version": 1,
            "categories": [
                {
                    "id": "dining",
                    "kind": "expense",
                    "priority": 10,
                    "rules": [{"type": "word", "patterns": ["uber eats"]}],
                },
                {
                    "id": "transport",
                    "kind": "expense",
                    "priority": 20,
                    "rules": [{"type": "word", "patterns": ["uber"]}],
                },
            ],
        },
    )
    rules = load_rules(target)
    assert classify("uber eats delivery", -1500, rules=rules) == "dining"
    assert classify("uber trip downtown", -1500, rules=rules) == "transport"


def test_the_shipped_file_carries_no_dead_pattern() -> None:
    """Asserted at exactly the loader's scope, not one step wider.

    Only ``literal`` clauses are checked, because only they are what the loader
    checks: a regex's source is source code, and demanding that no sibling
    match it would fail the very rule
    ``test_a_regex_is_never_accused_of_being_dead_on_its_own_source_text``
    argues must be allowed. A test that asserts more than the code promises
    turns a legitimate future edit into a mysterious red.
    """
    for rule in default_rules().categories:
        for index, clause in enumerate(rule.clauses):
            if not clause.literal:
                continue
            others = [c.pattern for i, c in enumerate(rule.clauses) if i != index]
            assert all(pattern.search(clause.source) is None for pattern in others), (
                f"{rule.id}: {clause.source!r} is already claimed by a sibling pattern"
            )


def test_a_duplicate_id_is_refused(tmp_path: Path) -> None:
    target = write_rules(
        tmp_path,
        {
            "version": 1,
            "categories": [
                {
                    "id": "dining",
                    "kind": "expense",
                    "priority": 10,
                    "rules": [{"type": "word", "patterns": ["aaa"]}],
                },
                {
                    "id": "dining",
                    "kind": "expense",
                    "priority": 20,
                    "rules": [{"type": "word", "patterns": ["bbb"]}],
                },
            ],
        },
    )
    with pytest.raises(RulesError, match="duplicate category id"):
        load_rules(target)


# ---------------------------------------------------------------------------
# transfer recognition: the one refusal M2.3 revoked, and the ones it did not
# ---------------------------------------------------------------------------


def transfer_rules(directory: Path, *patterns: str) -> Path:
    """A single ``transfer`` category holding ``patterns``, and nothing else."""
    return write_rules(
        directory,
        one_category(
            id="internal",
            kind="transfer",
            rules=[{"type": "word", "patterns": list(patterns)}],
        ),
    )


def test_a_transfer_category_may_declare_patterns(tmp_path: Path) -> None:
    """This asserts the opposite of what it asserted before P2 M2.3.

    Until M2.3 the loader raised on any ``transfer`` category that declared a
    pattern, and this test asserted the raise. The reason was that nothing read
    those patterns, and ``docs/STATUS.md`` §5.39 calls a rule no code evaluates
    "coverage that does not exist" -- a standard this file still holds to.
    What changed is the premise, not the standard: ``matches_transfer`` reads
    them now. The test is rewritten in place rather than deleted so that the
    reversal is something a reader can see, instead of an absence.
    """
    target = transfer_rules(tmp_path, "transfer to savings")
    rules = load_rules(target)
    assert rules.rows() == (("internal", None, "transfer"),)
    assert matches_transfer("Online Transfer To Savings account", rules=rules) == "internal"


def test_a_transfer_category_without_patterns_is_still_accepted(tmp_path: Path) -> None:
    """Unchanged by M2.3, deliberately.

    Only the refusal of transfer categories that *do* declare patterns was
    revoked. Demanding that they declare some would be a second, independent
    change riding along with the first, so this behaviour is left where it was
    found: the category loads, and ``matches_transfer`` never returns it.
    """
    target = write_rules(tmp_path, one_category(id="internal", kind="transfer", rules=[]))
    rules = load_rules(target)
    assert rules.rows() == (("internal", None, "transfer"),)
    assert matches_transfer("online transfer to savings", rules=rules) is None


def test_a_short_transfer_pattern_is_refused_like_any_other(tmp_path: Path) -> None:
    """Negative: `MIN_WORD_LENGTH` did not stop applying because the kind changed."""
    target = transfer_rules(tmp_path, "to")
    with pytest.raises(RulesError, match="shorter than"):
        load_rules(target)


def test_a_transfer_pattern_of_the_minimum_length_is_accepted(tmp_path: Path) -> None:
    """Positive for the refusal above: the length rule, not transfers, is the gate."""
    word = "a" * MIN_WORD_LENGTH
    target = transfer_rules(tmp_path, word)
    rules = load_rules(target)
    assert rules.categories[0].sources == (word,)
    assert matches_transfer(f"wire {word} confirmation", rules=rules) == "internal"


def test_a_transfer_pattern_broad_enough_to_be_a_fallback_is_refused(tmp_path: Path) -> None:
    """Negative: a catch-all in the transfer kind empties the totals, not a pie slice."""
    target = write_rules(
        tmp_path,
        one_category(id="internal", kind="transfer", rules=[{"type": "regex", "pattern": ".*"}]),
    )
    with pytest.raises(RulesError, match="becomes the silent fallback"):
        load_rules(target)


def test_a_narrow_transfer_regex_is_accepted(tmp_path: Path) -> None:
    """Positive for the refusal above, and the only regex clause in this section."""
    target = write_rules(
        tmp_path,
        one_category(
            id="internal",
            kind="transfer",
            rules=[{"type": "regex", "pattern": r"\btransfer to (savings|checking)\b"}],
        ),
    )
    rules = load_rules(target)
    assert matches_transfer("online transfer to checking", rules=rules) == "internal"
    assert matches_transfer("transfer to a roofing company", rules=rules) is None


def test_a_dead_transfer_pattern_is_refused(tmp_path: Path) -> None:
    """Negative: ``transfer`` swallows ``transfer to savings`` inside one category."""
    target = transfer_rules(tmp_path, "transfer to savings", "transfer")
    with pytest.raises(RulesError, match="already matched by"):
        load_rules(target)


def test_two_transfer_patterns_that_do_not_overlap_are_accepted(tmp_path: Path) -> None:
    """Positive for the refusal above: siblings are fine until one covers the other."""
    target = transfer_rules(tmp_path, "transfer to savings", "credit card payment")
    rules = load_rules(target)
    assert matches_transfer("transfer to savings", rules=rules) == "internal"
    assert matches_transfer("credit card payment", rules=rules) == "internal"


def test_two_transfer_categories_cannot_share_a_priority(tmp_path: Path) -> None:
    """Negative: transfers compete with each other, so their priorities must be unique."""
    target = write_rules(
        tmp_path,
        {
            "version": 1,
            "categories": [
                {
                    "id": "one",
                    "kind": "transfer",
                    "priority": 10,
                    "rules": [{"type": "word", "patterns": ["transfer to savings"]}],
                },
                {
                    "id": "two",
                    "kind": "transfer",
                    "priority": 10,
                    "rules": [{"type": "word", "patterns": ["credit card payment"]}],
                },
            ],
        },
    )
    with pytest.raises(RulesError, match="priority 10 used twice"):
        load_rules(target)


def test_transfer_is_its_own_priority_band(tmp_path: Path) -> None:
    """Positive: the number is unique *within* a kind, and transfer is a kind.

    An expense rule and a transfer rule never compete for one answer -- they are
    read by two different functions -- so making them share 10 must stay legal.
    """
    target = write_rules(
        tmp_path,
        {
            "version": 1,
            "categories": [
                {
                    "id": "dining",
                    "kind": "expense",
                    "priority": 10,
                    "rules": [{"type": "word", "patterns": ["chipotle"]}],
                },
                {
                    "id": "salary",
                    "kind": "income",
                    "priority": 10,
                    "rules": [{"type": "word", "patterns": ["payroll"]}],
                },
                {
                    "id": "internal",
                    "kind": "transfer",
                    "priority": 10,
                    "rules": [{"type": "word", "patterns": ["transfer to savings"]}],
                },
            ],
        },
    )
    rules = load_rules(target)
    assert len(rules.categories) == 3
    assert classify("chipotle downtown", -1200, rules=rules) == "dining"
    assert classify("payroll deposit", 1200, rules=rules) == "salary"
    assert matches_transfer("transfer to savings", rules=rules) == "internal"


def test_the_lower_transfer_priority_number_wins(tmp_path: Path) -> None:
    """Ordering means the same thing here as it does for the other kinds."""
    target = write_rules(
        tmp_path,
        {
            "version": 1,
            "categories": [
                {
                    "id": "late",
                    "kind": "transfer",
                    "priority": 90,
                    "rules": [{"type": "word", "patterns": ["transfer to savings"]}],
                },
                {
                    "id": "early",
                    "kind": "transfer",
                    "priority": 10,
                    "rules": [{"type": "word", "patterns": ["scheduled transfer to savings"]}],
                },
            ],
        },
    )
    rules = load_rules(target)
    assert matches_transfer("scheduled transfer to savings", rules=rules) == "early"
    assert matches_transfer("one time transfer to savings", rules=rules) == "late"


def test_matches_transfer_ignores_the_sign_of_the_amount(tmp_path: Path) -> None:
    """Direction is a fact about the movement, not about who owns the far end.

    A card payment leaves the account and the savings transfer that funded it
    arrives; both are the same event. ``matches_transfer`` takes no amount at
    all, so what this pins is the consequence: the two amounts below land on
    opposite sides of the ledger, and the transfer answer does not move.
    """
    target = transfer_rules(tmp_path, "transfer to savings")
    rules = load_rules(target)
    outgoing, incoming = -25_000, 25_000
    assert side_for(outgoing) != side_for(incoming)
    for amount_minor in (outgoing, incoming):
        assert classify("online transfer to savings", amount_minor, rules=rules) is None
    assert matches_transfer("online transfer to savings", rules=rules) == "internal"


def test_classify_never_returns_a_transfer_category(tmp_path: Path) -> None:
    """The sign gate consults ``side_for``, which answers income or expense only."""
    target = write_rules(
        tmp_path,
        {
            "version": 1,
            "categories": [
                {
                    "id": "internal",
                    "kind": "transfer",
                    "priority": 10,
                    "rules": [{"type": "word", "patterns": ["transfer to savings"]}],
                },
                {
                    "id": "salary",
                    "kind": "income",
                    "priority": 10,
                    "rules": [{"type": "word", "patterns": ["payroll"]}],
                },
            ],
        },
    )
    rules = load_rules(target)
    for amount_minor in (-25_000, 0, 25_000):
        assert classify("online transfer to savings", amount_minor, rules=rules) is None
    # Not passing because the file failed to load anything: the income rule in
    # the same file still answers.
    assert classify("payroll deposit", 25_000, rules=rules) == "salary"
    assert matches_transfer("online transfer to savings", rules=rules) == "internal"


def test_the_shipped_transfer_patterns_are_invisible_to_classify() -> None:
    """Every shipped transfer pattern, both directions, against the real file."""
    for rule in default_rules().categories:
        if rule.kind != "transfer":
            continue
        for clause in rule.clauses:
            for amount_minor in (-25_000, 25_000):
                assert classify(clause.source, amount_minor) != rule.id


def test_the_shipped_file_declares_transfer_and_manual_investment_categories() -> None:
    """The generic transfer keeps its rules; investment stays manual-only."""
    transfers = [rule for rule in default_rules().categories if rule.kind == "transfer"]
    assert [rule.id for rule in transfers] == [TRANSFER_CATEGORY_ID, "investment"]
    assert transfers[0].clauses, "the transfer category is no longer the empty declaration"
    assert not transfers[1].clauses, "investment must not guess from a platform name"


def test_the_manual_investment_category_is_never_returned_by_transfer_matching() -> None:
    """Principal, proceeds, fees and rewards can share one platform descriptor."""
    assert matches_transfer("investment platform payment") is None
    assert matches_transfer("digital asset purchase") is None


def test_shipped_pet_and_rewards_categories_keep_purchase_and_income_sides_separate() -> None:
    assert classify("Card Purchase Veterinary Hospital", -22_852) == "pet"
    assert classify("Card Purchase Veterinary Hospital", 22_852) is None
    assert classify("Cash Redemption", 16_309) == "rewards"
    assert classify("Cash Redemption", -16_309) is None


def test_cash_deposit_is_income_but_a_remote_deposit_does_not_guess_its_source() -> None:
    assert classify("ATM Cash Deposit", 32_000) == "cash-deposit"
    assert classify("ATM Cash Deposit", -32_000) != "cash-deposit"
    assert classify("Remote Online Deposit", 40_000) is None


def test_pay_in_4_is_a_transfer_mechanism_not_pet_income_or_cash() -> None:
    descriptor = "Pay IN 4 Deposit - POS Debit Veterinary Hospital"
    assert matches_transfer(descriptor) == TRANSFER_CATEGORY_ID
    assert classify(descriptor, 22_852) is None


def test_every_shipped_transfer_pattern_matches_its_own_text() -> None:
    """Without this, every negative below would also pass on patterns that match nothing."""
    for rule in default_rules().categories:
        if rule.kind != "transfer":
            continue
        for clause in rule.clauses:
            assert clause.literal, f"{clause.source!r} is a regex; this test samples literals"
            assert matches_transfer(clause.source) == rule.id
            assert matches_transfer(f"web {clause.source} confirmation") == rule.id


@pytest.mark.parametrize(
    "descriptor",
    [
        "Card Purchase        Some Shop",
        "Recurring Card Purchase",
        "Wire transfer to a roofing company",
        "Autopay to the electric company",
        "Payment to a landscaping service",
        "Online payment to a phone company",
    ],
)
def test_an_everyday_expense_is_not_claimed_as_a_transfer(descriptor: str) -> None:
    """A false positive here does not misfile a row -- it removes it from spending.

    Both cash-flow aggregations exclude transactions marked as transfers. The
    predecessor counted transfers as spending; over-claiming here would hide
    real spending instead, which is the same lie facing the other way.
    """
    assert matches_transfer(descriptor) is None


def test_a_bare_zelle_word_is_not_a_transfer_rule() -> None:
    """The recognisable transfer this engine deliberately declines to recognise.

    Most Zelle activity is a real payment to another person, so ``\\bzelle\\b``
    would move genuine spending out of the totals wholesale. A Zelle to
    *yourself* is a real single-sided transfer, but nothing in the descriptor
    separates it from a Zelle to anybody else -- so it is left to route 3, a
    person marking the row, rather than guessed at here.
    """
    for descriptor in (
        "Zelle payment to a friend",
        "Zelle payment from a friend",
        "ZELLE TO MYSELF",
    ):
        assert matches_transfer(descriptor) is None
    sources = [source for rule in default_rules().categories for source in rule.sources]
    assert not any("zelle" in source for source in sources)


def test_matches_transfer_returns_none_rather_than_a_bucket() -> None:
    """No fallback here either: unrecognised is an answer, not a category."""
    assert matches_transfer("zqxjvk wombat consortium") is None


# ---------------------------------------------------------------------------
# matching is case- and boundary-correct
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "descriptor",
    ["CHIPOTLE 1234", "chipotle", "Chipotle Mexican Grill", "lunch at CHIPOTLE."],
)
def test_matching_ignores_case_and_surrounding_text(tmp_path: Path, descriptor: str) -> None:
    target = write_rules(tmp_path, one_category())
    assert classify(descriptor, -900, rules=load_rules(target)) == "dining"


def test_a_pattern_ending_in_punctuation_still_matches(tmp_path: Path) -> None:
    """``\\b`` after a non-word character demands a word character; lookahead does not."""
    target = write_rules(
        tmp_path, one_category(rules=[{"type": "word", "patterns": ["apple.com/bill"]}])
    )
    rules = load_rules(target)
    assert classify("APPLE.COM/BILL RECURRING", -299, rules=rules) == "dining"


# ---------------------------------------------------------------------------
# assign_categories: which leg, and which text
# ---------------------------------------------------------------------------


class _Posting:
    def __init__(self, identifier: str, seq: int, amount_minor: int) -> None:
        self.id = identifier
        self.seq = seq
        self.amount_minor = amount_minor


class _Identity:
    def __init__(self, raw_descriptor: str) -> None:
        self.raw_descriptor = raw_descriptor


class _Entry:
    def __init__(self, descriptor: str, amount_minor: int) -> None:
        self.postings = (
            _Posting("bank-leg", 0, amount_minor),
            _Posting("counter-leg", 1, -amount_minor),
        )
        self.identity = _Identity(descriptor)


def test_the_category_lands_on_the_bank_leg_only() -> None:
    """``v_transaction`` joins that leg; a category anywhere else is invisible."""
    assignments = assign_categories([_Entry("CHIPOTLE 1234", -1200)])
    assert assignments == {"bank-leg": "dining"}
    assert "counter-leg" not in assignments


def test_an_unmatched_line_is_assigned_none_rather_than_omitted() -> None:
    """Omitting it would leave a stale category behind on re-categorisation."""
    assignments = assign_categories([_Entry("zqxjvk wombat consortium", -1200)])
    assert assignments == {"bank-leg": None}


def test_a_recognised_transfer_does_not_land_in_posting_category_id() -> None:
    """M2.3 added a reader, not a writer.

    ``assign_categories`` still records only what ``classify`` returns, so a
    line the transfer rules claim leaves ``posting.category_id`` NULL. Whatever
    sets ``txn.is_transfer`` is a separate decision on a separate column, and
    this pins that the two did not quietly become one.
    """
    descriptor = "CREDIT CARD PAYMENT"
    assert matches_transfer(descriptor) == TRANSFER_CATEGORY_ID
    assert assign_categories([_Entry(descriptor, -25_000)]) == {"bank-leg": None}


def test_the_shipped_file_is_where_default_rules_reads_from() -> None:
    assert RULES_PATH.is_file()
    assert load_rules(RULES_PATH).ids() == default_rules().ids()
