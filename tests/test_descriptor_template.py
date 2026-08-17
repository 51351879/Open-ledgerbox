# SPDX-License-Identifier: AGPL-3.0-or-later
"""The descriptor template is the unit learning happens at.

A Chase descriptor embeds dates, card fragments and reference numbers, so the
same coffee shop never produces byte-identical descriptors twice and learning
keyed on exact bytes would almost never fire. The template strips exactly the
parts that vary per visit and keeps every part that identifies the counterparty,
because merging two different payees would let one person's answer claim another
person's money.
"""

from __future__ import annotations

import pytest

from ledgerbox.descriptor_template import TEMPLATE_VERSION, descriptor_template


def test_the_same_merchant_on_two_days_is_one_template() -> None:
    monday = descriptor_template("Card Purchase 03/12 Sq *Blue Bottle 4471 Ref 9982211")
    friday = descriptor_template("Card Purchase 03/16 Sq *Blue Bottle 5512 Ref 1120394")

    assert monday == friday


def test_two_zelle_recipients_are_never_one_template() -> None:
    """Names differ by letters, not digits; merging them would misfile money."""
    first = descriptor_template("Zelle Payment To John Doe 991101")
    second = descriptor_template("Zelle Payment To Jane Smith 991102")

    assert first != second


def test_dates_card_fragments_and_reference_runs_are_masked() -> None:
    template = descriptor_template("CARD PURCHASE 03/12 STARBUCKS #0921 SEATTLE 4471")

    assert "03" not in template
    assert "0921" not in template
    assert "4471" not in template
    assert "STARBUCKS" in template


def test_single_digits_survive_because_brands_carry_them() -> None:
    assert "7-ELEVEN" in descriptor_template("Card Purchase 7-Eleven Store")


def test_case_and_whitespace_do_not_split_a_merchant() -> None:
    assert descriptor_template("  sq  *coffee   shop ") == descriptor_template("SQ *COFFEE SHOP")


def test_the_template_is_a_fixed_point() -> None:
    """Templating a template changes nothing, so re-deriving is always safe."""
    once = descriptor_template("Card Purchase 03/12 Sq *Blue Bottle 4471")

    assert descriptor_template(once) == once


def test_non_ascii_text_passes_through_intact() -> None:
    assert "全家便利店" in descriptor_template("消费 全家便利店 260301")


@pytest.mark.parametrize("empty", ["", "   ", "0312 4471 998"])
def test_a_descriptor_with_nothing_but_noise_yields_the_empty_template(empty: str) -> None:
    """An all-noise descriptor identifies nobody; learning must never key on it."""
    assert descriptor_template(empty) == ""


def test_the_version_is_exported_so_stored_templates_can_be_rederived() -> None:
    assert isinstance(TEMPLATE_VERSION, int) and TEMPLATE_VERSION >= 1
