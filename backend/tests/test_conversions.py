from decimal import Decimal

import pytest

from forex_ledger.calc.conversions import analyze_conversion
from forex_ledger.calc.gst import conversion_gst
from forex_ledger.money import q


def test_deposit_markup_matches_spec_example():
    # ₹97,000 → $1,000 while the market was ₹96: ₹1,000 hidden markup.
    result = analyze_conversion("deposit", Decimal("97000"), Decimal("1000"), Decimal("96"))
    assert result.rate_got == Decimal("97")
    assert result.markup_inr == Decimal("1000")
    assert q(result.markup_pct, 3) == Decimal("1.042")
    assert result.hidden_cost_inr == Decimal("1000")


def test_withdrawal_markup_matches_spec_example():
    # $1,000 → ₹98,000 while the market was ₹99: ₹1,000 hidden markup.
    result = analyze_conversion("withdrawal", Decimal("98000"), Decimal("1000"), Decimal("99"))
    assert result.rate_got == Decimal("98")
    assert result.markup_inr == Decimal("1000")
    assert q(result.markup_pct, 3) == Decimal("1.010")


def test_deposit_excludes_charges_and_tcs_from_the_rate():
    result = analyze_conversion(
        "deposit",
        Decimal("100180"),  # ₹1,00,000 converted + ₹180 GST
        Decimal("1041.67"),
        Decimal("95"),
        charges_inr=Decimal("180"),
    )
    assert result.converted_inr == Decimal("100000")
    assert result.charges_inr == Decimal("180")
    assert result.hidden_cost_inr == result.markup_inr + Decimal("180")

    with_tcs = analyze_conversion(
        "deposit", Decimal("120000"), Decimal("1000"), Decimal("96"), tcs_inr=Decimal("20000")
    )
    assert with_tcs.converted_inr == Decimal("100000")  # TCS is not a cost of conversion


def test_withdrawal_adds_back_deducted_charges():
    result = analyze_conversion(
        "withdrawal", Decimal("97820"), Decimal("1000"), Decimal("99"), charges_inr=Decimal("180")
    )
    assert result.rate_got == Decimal("98")
    assert result.markup_inr == Decimal("1000")
    assert result.hidden_cost_inr == Decimal("1180")


def test_bank_comparison_sign_means_platform_better():
    deposit = analyze_conversion(
        "deposit", Decimal("97000"), Decimal("1000"), Decimal("96"), bank_rate=Decimal("97.5")
    )
    assert deposit.vs_bank_inr == Decimal("500")  # bank would have charged ₹97,500

    withdrawal = analyze_conversion(
        "withdrawal", Decimal("98000"), Decimal("1000"), Decimal("99"), bank_rate=Decimal("98.4")
    )
    assert withdrawal.vs_bank_inr == Decimal("-400")  # bank would have paid ₹98,400


def test_invalid_amounts_rejected():
    with pytest.raises(ValueError):
        analyze_conversion("deposit", Decimal("100"), Decimal("0"), Decimal("96"))
    with pytest.raises(ValueError):
        analyze_conversion("deposit", Decimal("100"), Decimal("1"), Decimal("96"), Decimal("100"))


@pytest.mark.parametrize(
    ("value", "gst"),
    [
        ("10000", "45"),  # minimum
        ("100000", "180"),
        ("500000", "540"),
        ("1000000", "990"),
        ("2000000", "1170"),
        ("100000000", "10800"),  # cap
    ],
)
def test_conversion_gst_slabs(value, gst):
    assert q(conversion_gst(Decimal(value))) == Decimal(gst)
