# sacco-ledger

[![CI](https://github.com/codesphere-u/sacco-ledger/actions/workflows/ci.yml/badge.svg)](https://github.com/codesphere-u/sacco-ledger/actions/workflows/ci.yml)
[![License: MIT](https://img.shields.io/badge/License-MIT-green.svg)](LICENSE)
[![Python 3.11+](https://img.shields.io/badge/python-3.11%2B-blue.svg)](pyproject.toml)
[![Zero Dependencies](https://img.shields.io/badge/runtime%20dependencies-0-brightgreen.svg)](pyproject.toml)

**Offline-first VSLA & SACCO cooperative member share-capital, M-of-N officer quorum loan approval, integer minor-unit amortization, end-of-cycle dividend share-out, and tamper-evident SHA-256 passbook engine.**

- **Mission Domain:** Financial Inclusion & Grassroots Cooperatives
- **Stack:** Pure Python 3.11+ (zero runtime third-party dependencies, `mypy --strict`, `ruff`, `pytest`)
- **Maintainer:** Codesphere Innovations (U) Limited (`codesphere-u`)

---

## Problem Solved

Hundreds of thousands of Village Savings and Loan Associations (VSLAs) and SACCOs manage community savings in paper passbooks or spreadsheets vulnerable to insider tampering, single-officer self-dealing, and rounding errors.

## Key Features

- SQLite WAL offline-first passbook with SHA-256 hash-chain tamper verification (`verify_passbook_chain`).
- Exact integer minor-unit reducing-balance and flat loan amortization (`sum(principal) == principal_minor`).
- M-of-N officer quorum loan disbursement with strict anti-self-approval and 3x share-savings borrowing caps.
- Strict `<=160`-char GSM-7 SMS member receipts and exact pro-rata end-of-cycle dividend share-out.

## Architecture Overview

```mermaid
flowchart LR
  A[Member Shares & Loan Requests] --> B[ledger.py<br/>SQLite WAL + Integer Math]
  B --> C[M-of-N Officer Quorum Gate]
  C --> D[Amortization & Repayments]
  D --> E[Pro-Rata Share-Out & SHA-256 Passbook]
```

See [docs/architecture.md](docs/architecture.md) for module-level design details.

## Installation

```bash
git clone https://github.com/codesphere-u/sacco-ledger.git
cd sacco-ledger
uv sync --extra dev
```

Or install in editable mode with standard `pip`:

```bash
pip install -e ".[dev]"
```

## Quick Start (CLI)

```bash
sacco-ledger demo
```

## Quick Start (Python API)

```python
from sacco_ledger import compute_amortization_schedule

sched = compute_amortization_schedule(100_000, monthly_rate_bps=500, term_months=3)
assert sum(s.principal_minor for s in sched) == 100_000
```

## Testing & Quality Gates

```bash
uv run ruff check src tests
uv run mypy src
uv run pytest -v
```

## Security, Contributing & Roadmap

- **Security:** See [SECURITY.md](SECURITY.md) for responsible disclosure procedures.
- **Contributing:** See [CONTRIBUTING.md](CONTRIBUTING.md) and [CODE_OF_CONDUCT.md](CODE_OF_CONDUCT.md).
- **Changelog & Roadmap:** See [CHANGELOG.md](CHANGELOG.md) and [ROADMAP.md](ROADMAP.md).

## License

MIT License — see [LICENSE](LICENSE).

## Support & Contact

Open an issue on [GitHub Issues](https://github.com/codesphere-u/sacco-ledger/issues) or contact `codesphereinnovationz@gmail.com`.
