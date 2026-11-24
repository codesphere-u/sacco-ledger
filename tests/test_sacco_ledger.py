"""Tests for VSLA/SACCO integer amortization, M-of-N officer quorum, share-out & passbook chain."""

from __future__ import annotations

import json

import pytest

from sacco_ledger import LoanMethod, SaccoLedger, compute_amortization_schedule
from sacco_ledger.cli import main as cli_main


def test_amortization_principal_reconciles_to_exact_integer_minor_units() -> None:
    sched = compute_amortization_schedule(100_000, monthly_rate_bps=500, term_months=3)
    assert sum(s.principal_minor for s in sched) == 100_000
    assert sched[-1].ending_balance_minor == 0
    # Reducing balance interest shrinks each period
    assert sched[0].interest_minor > sched[1].interest_minor > sched[2].interest_minor

    flat = compute_amortization_schedule(
        100_000, monthly_rate_bps=500, term_months=3, method=LoanMethod.FLAT
    )
    assert flat[0].interest_minor == flat[1].interest_minor == 5_000


def test_quorum_prevents_self_approval_non_officer_and_tampered_passbook() -> None:
    ledger = SaccoLedger(":memory:", currency="UGX", share_price_minor=10_000, quorum_required=2)
    ledger.register_member("CHAIR", "Chair", "+2567001", is_officer=True)
    ledger.register_member("TREAS", "Treasurer", "+2567002", is_officer=True)
    ledger.register_member("MEM1", "Member One", "+2567003", is_officer=False)

    r_sms = ledger.buy_shares("CHAIR", 10)
    assert len(r_sms) <= 160 and r_sms.isascii()
    ledger.buy_shares("MEM1", 10)

    # Exceeds 3x share limit (300,000)
    with pytest.raises(ValueError, match="exceeds 3x"):
        ledger.request_loan("L-BIG", "MEM1", principal_minor=400_000, monthly_rate_bps=500, term_months=3)

    sched = ledger.request_loan("L-1", "CHAIR", principal_minor=150_000, monthly_rate_bps=400, term_months=3)
    # Self-approval forbidden
    with pytest.raises(PermissionError, match="Self-approval"):
        ledger.approve_loan("L-1", "CHAIR")
    # Non-officer forbidden
    with pytest.raises(PermissionError, match="not an authorized"):
        ledger.approve_loan("L-1", "MEM1")

    # Need 2 votes; 1 vote keeps status pending_quorum
    assert ledger.approve_loan("L-1", "TREAS") == "pending_quorum"
    ledger.register_member("SEC", "Secretary", "+2567004", is_officer=True)
    assert ledger.approve_loan("L-1", "SEC") == "disbursed"

    total_due = sum(s.total_due_minor for s in sched)
    repay_sms = ledger.repay_loan("L-1", total_due)
    assert "Bal:0 UGX" in repay_sms and len(repay_sms) <= 160

    payouts = ledger.calculate_share_out()
    expected_interest = total_due - 150_000
    assert sum(p.dividend_minor for p in payouts) == expected_interest

    ok, _ = ledger.verify_passbook_chain()
    assert ok is True
    ledger._conn.execute("UPDATE passbook_entries SET amount_minor = 999999 WHERE seq = 4")
    bad_ok, msg = ledger.verify_passbook_chain()
    assert bad_ok is False and "Tampered" in msg
    ledger.close()


def test_cli_demo(capsys: pytest.CaptureFixture[str]) -> None:
    assert cli_main(["demo"]) == 0
    out = json.loads(capsys.readouterr().out)
    assert out["passbook_valid"] is True
    assert out["loan_status_after_quorum"] == "disbursed"
