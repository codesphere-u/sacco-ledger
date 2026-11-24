"""Offline-first VSLA/SACCO SQLite passbook, M-of-N quorum loan approval & share-out engine."""

from __future__ import annotations

import hashlib
import json
import sqlite3
from dataclasses import dataclass
from datetime import UTC, datetime
from enum import StrEnum
from pathlib import Path
from typing import Any


class LoanMethod(StrEnum):
    REDUCING_BALANCE = "reducing_balance"
    FLAT = "flat"


@dataclass(frozen=True)
class Installment:
    period: int
    principal_minor: int
    interest_minor: int
    total_due_minor: int
    ending_balance_minor: int


@dataclass(frozen=True)
class ShareOutPayout:
    member_id: str
    full_name: str
    shares_held: int
    share_capital_minor: int
    dividend_minor: int
    total_payout_minor: int


def compute_amortization_schedule(
    principal_minor: int,
    *,
    monthly_rate_bps: int,
    term_months: int,
    method: LoanMethod = LoanMethod.REDUCING_BALANCE,
) -> tuple[Installment, ...]:
    """Compute exact integer minor-unit loan repayment schedule (sum(principal) == principal_minor)."""
    if principal_minor <= 0 or term_months <= 0 or monthly_rate_bps < 0:
        raise ValueError("principal_minor and term_months must be > 0; monthly_rate_bps >= 0")

    base_principal = principal_minor // term_months
    remainder = principal_minor - (base_principal * term_months)
    balance = principal_minor
    schedule: list[Installment] = []

    for period in range(1, term_months + 1):
        p_due = base_principal + (remainder if period == term_months else 0)
        if method == LoanMethod.FLAT:
            interest = (principal_minor * monthly_rate_bps + 5_000) // 10_000
        else:
            interest = (balance * monthly_rate_bps + 5_000) // 10_000
        balance -= p_due
        schedule.append(
            Installment(
                period=period,
                principal_minor=p_due,
                interest_minor=interest,
                total_due_minor=p_due + interest,
                ending_balance_minor=balance,
            )
        )
    return tuple(schedule)


def _canonical_json(obj: Any) -> str:
    return json.dumps(obj, sort_keys=True, separators=(",", ":"), ensure_ascii=True)


class SaccoLedger:
    """Offline SQLite VSLA/SACCO cooperative passbook with quorum loan controls & hash chain."""

    def __init__(
        self,
        db_path: str | Path = ":memory:",
        *,
        currency: str = "UGX",
        share_price_minor: int = 10_000,
        quorum_required: int = 2,
    ) -> None:
        if share_price_minor <= 0 or quorum_required < 1:
            raise ValueError("share_price_minor must be > 0 and quorum_required >= 1")
        self.currency = currency.upper()
        self.share_price_minor = share_price_minor
        self.quorum_required = quorum_required
        self.db_path = str(db_path)
        if self.db_path != ":memory:":
            Path(self.db_path).parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(self.db_path)
        self._conn.row_factory = sqlite3.Row
        self._init_schema()

    def close(self) -> None:
        self._conn.close()

    def _init_schema(self) -> None:
        self._conn.executescript(
            """
            PRAGMA journal_mode=WAL;
            CREATE TABLE IF NOT EXISTS members (
                member_id TEXT PRIMARY KEY,
                full_name TEXT NOT NULL,
                phone TEXT NOT NULL,
                is_officer INTEGER NOT NULL DEFAULT 0,
                shares_held INTEGER NOT NULL DEFAULT 0,
                created_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS loans (
                loan_id TEXT PRIMARY KEY,
                member_id TEXT NOT NULL,
                principal_minor INTEGER NOT NULL,
                monthly_rate_bps INTEGER NOT NULL,
                term_months INTEGER NOT NULL,
                method TEXT NOT NULL,
                total_due_minor INTEGER NOT NULL,
                repaid_minor INTEGER NOT NULL DEFAULT 0,
                status TEXT NOT NULL,
                created_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS loan_approvals (
                loan_id TEXT NOT NULL,
                officer_id TEXT NOT NULL,
                approved_at TEXT NOT NULL,
                PRIMARY KEY (loan_id, officer_id)
            );
            CREATE TABLE IF NOT EXISTS passbook_entries (
                seq INTEGER PRIMARY KEY AUTOINCREMENT,
                event_type TEXT NOT NULL,
                member_id TEXT NOT NULL,
                amount_minor INTEGER NOT NULL,
                payload_json TEXT NOT NULL,
                prev_hash TEXT NOT NULL,
                entry_hash TEXT NOT NULL,
                created_at TEXT NOT NULL
            );
            """
        )
        self._conn.commit()

    def register_member(
        self,
        member_id: str,
        full_name: str,
        phone: str,
        *,
        is_officer: bool = False,
    ) -> None:
        iso = datetime.now(UTC).isoformat()
        with self._conn:
            self._conn.execute(
                """
                INSERT INTO members (member_id, full_name, phone, is_officer, created_at)
                VALUES (?, ?, ?, ?, ?)
                """,
                (member_id, full_name, phone, 1 if is_officer else 0, iso),
            )
            self._append_passbook(
                "MEMBER_JOINED", member_id, 0, {"full_name": full_name, "is_officer": is_officer}, iso
            )

    def buy_shares(self, member_id: str, num_shares: int) -> str:
        """Record share purchase and return a GSM-7 <=160-char SMS member receipt."""
        if num_shares <= 0:
            raise ValueError("num_shares must be positive")
        row = self._conn.execute("SELECT * FROM members WHERE member_id = ?", (member_id,)).fetchone()
        if row is None:
            raise KeyError(f"Unknown member '{member_id}'")
        amount = num_shares * self.share_price_minor
        new_shares = int(row["shares_held"]) + num_shares
        iso = datetime.now(UTC).isoformat()
        with self._conn:
            self._conn.execute(
                "UPDATE members SET shares_held = ? WHERE member_id = ?",
                (new_shares, member_id),
            )
            entry_hash = self._append_passbook(
                "SHARE_PURCHASE",
                member_id,
                amount,
                {"num_shares": num_shares, "total_shares": new_shares},
                iso,
            )
        receipt = (
            f"SACCO RECEIPT {member_id}: +{num_shares} sh ({amount} {self.currency}). "
            f"Total:{new_shares} sh. Ref:{entry_hash[:10]}"
        )
        return receipt[:160]

    def request_loan(
        self,
        loan_id: str,
        member_id: str,
        *,
        principal_minor: int,
        monthly_rate_bps: int,
        term_months: int,
        method: LoanMethod = LoanMethod.REDUCING_BALANCE,
    ) -> tuple[Installment, ...]:
        """Submit a member loan request (capped at 3x member share savings; requires officer quorum)."""
        row = self._conn.execute("SELECT * FROM members WHERE member_id = ?", (member_id,)).fetchone()
        if row is None:
            raise KeyError(f"Unknown member '{member_id}'")
        max_eligible = int(row["shares_held"]) * self.share_price_minor * 3
        if principal_minor > max_eligible:
            raise ValueError(
                f"Loan principal ({principal_minor}) exceeds 3x member share savings ({max_eligible})"
            )
        schedule = compute_amortization_schedule(
            principal_minor,
            monthly_rate_bps=monthly_rate_bps,
            term_months=term_months,
            method=method,
        )
        total_due = sum(s.total_due_minor for s in schedule)
        iso = datetime.now(UTC).isoformat()
        with self._conn:
            self._conn.execute(
                """
                INSERT INTO loans (
                    loan_id, member_id, principal_minor, monthly_rate_bps,
                    term_months, method, total_due_minor, status, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, 'pending_quorum', ?)
                """,
                (loan_id, member_id, principal_minor, monthly_rate_bps, term_months, method.value, total_due, iso),
            )
            self._append_passbook(
                "LOAN_REQUESTED",
                member_id,
                principal_minor,
                {"loan_id": loan_id, "total_due_minor": total_due, "term_months": term_months},
                iso,
            )
        return schedule

    def approve_loan(self, loan_id: str, officer_id: str) -> str:
        """Record an officer approval vote; disburses only when M-of-N non-borrower quorum is met."""
        loan = self._conn.execute("SELECT * FROM loans WHERE loan_id = ?", (loan_id,)).fetchone()
        if loan is None:
            raise KeyError(f"Unknown loan '{loan_id}'")
        officer = self._conn.execute("SELECT * FROM members WHERE member_id = ?", (officer_id,)).fetchone()
        if officer is None or not bool(officer["is_officer"]):
            raise PermissionError(f"Member '{officer_id}' is not an authorized SACCO officer")
        if officer_id == loan["member_id"]:
            raise PermissionError("Self-approval forbidden: borrower cannot approve their own loan")

        iso = datetime.now(UTC).isoformat()
        with self._conn:
            self._conn.execute(
                "INSERT INTO loan_approvals (loan_id, officer_id, approved_at) VALUES (?, ?, ?)",
                (loan_id, officer_id, iso),
            )
            votes = int(
                self._conn.execute(
                    "SELECT COUNT(*) FROM loan_approvals WHERE loan_id = ?", (loan_id,)
                ).fetchone()[0]
            )
            new_status = "disbursed" if votes >= self.quorum_required else "pending_quorum"
            self._conn.execute("UPDATE loans SET status = ? WHERE loan_id = ?", (new_status, loan_id))
            self._append_passbook(
                "LOAN_APPROVAL_VOTE",
                str(loan["member_id"]),
                int(loan["principal_minor"]) if new_status == "disbursed" else 0,
                {"loan_id": loan_id, "officer_id": officer_id, "votes": votes, "status": new_status},
                iso,
            )
        return new_status

    def repay_loan(self, loan_id: str, amount_minor: int) -> str:
        """Record a loan repayment and return a GSM-7 <=160-char SMS confirmation receipt."""
        if amount_minor <= 0:
            raise ValueError("Repayment amount must be positive")
        loan = self._conn.execute("SELECT * FROM loans WHERE loan_id = ?", (loan_id,)).fetchone()
        if loan is None:
            raise KeyError(f"Unknown loan '{loan_id}'")
        if loan["status"] not in ("disbursed", "repaid"):
            raise ValueError(f"Loan '{loan_id}' is not disbursed (status={loan['status']})")

        new_repaid = int(loan["repaid_minor"]) + amount_minor
        remaining = max(0, int(loan["total_due_minor"]) - new_repaid)
        status = "repaid" if remaining == 0 else "disbursed"
        iso = datetime.now(UTC).isoformat()
        with self._conn:
            self._conn.execute(
                "UPDATE loans SET repaid_minor = ?, status = ? WHERE loan_id = ?",
                (new_repaid, status, loan_id),
            )
            entry_hash = self._append_passbook(
                "LOAN_REPAYMENT",
                str(loan["member_id"]),
                amount_minor,
                {"loan_id": loan_id, "remaining_minor": remaining, "status": status},
                iso,
            )
        receipt = (
            f"SACCO REPAY {loan_id}: Paid {amount_minor} {self.currency}. "
            f"Bal:{remaining} {self.currency}. Ref:{entry_hash[:10]}"
        )
        return receipt[:160]

    def calculate_share_out(self) -> list[ShareOutPayout]:
        """Compute end-of-cycle VSLA pro-rata share-out dividends from realized loan interest."""
        members = self._conn.execute(
            "SELECT member_id, full_name, shares_held FROM members ORDER BY member_id ASC"
        ).fetchall()
        total_shares = sum(int(m["shares_held"]) for m in members)
        if total_shares == 0:
            return []

        # Realized interest surplus = sum(max(0, repaid - principal)) across loans
        loans = self._conn.execute("SELECT principal_minor, repaid_minor FROM loans").fetchall()
        interest_pool = sum(max(0, int(r["repaid_minor"]) - int(r["principal_minor"])) for r in loans)

        payouts: list[ShareOutPayout] = []
        distributed = 0
        for idx, m in enumerate(members):
            shares = int(m["shares_held"])
            capital = shares * self.share_price_minor
            if idx == len(members) - 1 and shares > 0:
                dividend = max(0, interest_pool - distributed)
            else:
                dividend = (interest_pool * shares) // total_shares
                distributed += dividend
            payouts.append(
                ShareOutPayout(
                    member_id=str(m["member_id"]),
                    full_name=str(m["full_name"]),
                    shares_held=shares,
                    share_capital_minor=capital,
                    dividend_minor=dividend,
                    total_payout_minor=capital + dividend,
                )
            )
        return payouts

    def verify_passbook_chain(self) -> tuple[bool, str]:
        """Verify SHA-256 hash-chain tamper resistance across every passbook entry."""
        rows = self._conn.execute(
            """
            SELECT seq, event_type, member_id, amount_minor, payload_json, prev_hash, entry_hash, created_at
            FROM passbook_entries ORDER BY seq ASC
            """
        ).fetchall()
        prev = "0" * 64
        for row in rows:
            if row["prev_hash"] != prev:
                return False, f"Chain break at seq={row['seq']}"
            material = (
                f"{row['seq']}|{row['event_type']}|{row['member_id']}|{row['amount_minor']}|"
                f"{row['payload_json']}|{row['prev_hash']}|{row['created_at']}"
            )
            computed = hashlib.sha256(material.encode("utf-8")).hexdigest()
            if computed != row["entry_hash"]:
                return False, f"Tampered passbook entry at seq={row['seq']}"
            prev = computed
        return True, f"Verified {len(rows)} passbook entries"

    def _append_passbook(
        self,
        event_type: str,
        member_id: str,
        amount_minor: int,
        payload: dict[str, Any],
        created_at: str,
    ) -> str:
        last = self._conn.execute(
            "SELECT seq, entry_hash FROM passbook_entries ORDER BY seq DESC LIMIT 1"
        ).fetchone()
        next_seq = (int(last["seq"]) + 1) if last else 1
        prev_hash = str(last["entry_hash"]) if last else ("0" * 64)
        pjson = _canonical_json(payload)
        material = f"{next_seq}|{event_type}|{member_id}|{amount_minor}|{pjson}|{prev_hash}|{created_at}"
        entry_hash = hashlib.sha256(material.encode("utf-8")).hexdigest()
        self._conn.execute(
            """
            INSERT INTO passbook_entries (
                seq, event_type, member_id, amount_minor, payload_json, prev_hash, entry_hash, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (next_seq, event_type, member_id, amount_minor, pjson, prev_hash, entry_hash, created_at),
        )
        return entry_hash
