# Petty Cash Management — Final Project Report

## 1. Architecture & Design
- **Direct GL Integration**: Funds link directly to Odoo Cash Journals (`account.journal`, `type='cash'`). Real-time balances aggregate dynamically from posted lines (`account.move.line`).
- **Concurrency & Immutability**: Row-level locking (`lock_for_update()`) protects against overdrafts and race conditions. Posted records and workflow fields are immutable.
- **Workflow & Controls**: Configurable approval threshold, per-expense limit, and low-balance alert configured per company under Settings. Enforces segregation of duties and receipt validation per category.
- **Ledger Sync**: Bidirectional sync between petty cash records and general ledger moves on draft, cancel, reverse, and re-post.

## 2. Security Matrix

| Role | Fund | Category | Expense | Top-up | Top-up Source | Accounting Group |
|---|---|---|---|---|---|---|
| **Custodian** | Read + Edit non-control | Read | CRUD (own funds) | Read (own funds) | None | None |
| **Manager (No Acct)** | Full CRUD, Activate, Close | Full CRUD | Full CRUD + Approve/Reject | Create/Read/Cancel Draft | Full CRUD | None |
| **Manager (+ Readonly)** | Full CRUD (can select journals) | Full CRUD | Full CRUD + Approve/Reject | Create/Read/Cancel Draft | Full CRUD (can select accounts) | `account.group_account_readonly` |
| **Manager (+ Invoicing)** | Full CRUD, Activate, Close | Full CRUD | Full CRUD + Cancel/Reverse + Approve/Reject | Full CRUD + Post | Full CRUD | `account.group_account_invoice` |

## 3. Journal Entry Structure
- **Disbursement**: `Dr` Expense Account (`category.expense_account_id`) / `Cr` Petty Cash Account (`fund.default_account_id`).
- **Reversal**: `Dr` Petty Cash Account / `Cr` Expense Account.
- **Top-up**: `Dr` Petty Cash Account / `Cr` Source Account (`source.account_id`).

## 4. Test Suite Summary
- `TestPettyCashFoundation`: 17 tests (setup, constraints, validation, balance compute, isolation).
- `TestPettyCashVoucher`: 27 tests (workflow, thresholds, receipts, ledger posting, reversal, concurrency).
- `TestPettyCashTopup`: 9 tests (creation, source validation, posting, sequences, fiscal lock dates).
- `TestAccountMoveSync`: 10 tests (sync on draft, cancel, reverse, re-post, multi-company, non-cash filter).
- **Total Post-Tests**: **63** (71 test executions, 0 failures).

## 5. Implementation Notes
- Scoped `sudo()` calls (3 total): balance aggregation (`petty_cash_fund.py`), voucher ledger posting (`petty_cash_voucher.py`), entry synchronization (`account_move.py`).
- Out of scope: Tax calculation engine, cash counts, custom PDF reports, multi-currency funds.
