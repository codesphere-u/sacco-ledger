"""CLI for sacco-ledger."""

from __future__ import annotations

import argparse
import json
import sys
from collections.abc import Sequence

from sacco_ledger.ledger import SaccoLedger


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="sacco-ledger",
        description="Offline-first VSLA/SACCO share-capital, quorum loan & hash-chained passbook CLI.",
    )
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("demo", help="Run a full VSLA savings -> 2-of-3 quorum loan -> repayment -> share-out demo")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if args.command == "demo":
        ledger = SaccoLedger(":memory:", currency="UGX", share_price_minor=10_000, quorum_required=2)
        ledger.register_member("M01", "Grace Akello", "+256772100001", is_officer=True)
        ledger.register_member("M02", "Peter Mugisha", "+256772100002", is_officer=True)
        ledger.register_member("M03", "Sarah Nambi", "+256772100003", is_officer=False)
        ledger.buy_shares("M01", 5)
        ledger.buy_shares("M02", 5)
        ledger.buy_shares("M03", 10)
        sched = ledger.request_loan("LN-01", "M03", principal_minor=200_000, monthly_rate_bps=500, term_months=4)
        ledger.approve_loan("LN-01", "M01")
        status = ledger.approve_loan("LN-01", "M02")
        total_due = sum(s.total_due_minor for s in sched)
        receipt = ledger.repay_loan("LN-01", total_due)
        payouts = ledger.calculate_share_out()
        valid, msg = ledger.verify_passbook_chain()
        print(
            json.dumps(
                {
                    "loan_status_after_quorum": status,
                    "repayment_sms": receipt,
                    "share_out": [
                        {"member": p.member_id, "capital": p.share_capital_minor, "dividend": p.dividend_minor}
                        for p in payouts
                    ],
                    "passbook_valid": valid,
                    "passbook_message": msg,
                },
                indent=2,
            )
        )
        ledger.close()
        return 0 if valid else 2
    return 1


if __name__ == "__main__":
    sys.exit(main())
