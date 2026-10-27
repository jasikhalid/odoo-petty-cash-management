# Petty Cash Management — Final Project Report

## 1. Features
- **Direct General Ledger Integration**: Each petty cash fund is linked directly to a dedicated Odoo Cash Journal (`account.journal` of type `cash`). Live balances are dynamically computed from posted journal items (`account.move.line`) without shadow tables or balance drift.
- **Strict Concurrency Protection**: High-concurrency disbursements employ PostgreSQL row-level locks (`SELECT ... FOR UPDATE`), fresh live balance recomputation under the lock, and validation to prevent negative cash balances and race conditions.
- **Workflow Protection & Immutability**: All workflow fields (`state`, `name`, `move_id`, audit dates/users) are strictly guarded against direct ORM/RPC modification. Posted records cannot be altered or deleted.
- **Configurable Multi-Tier Approvals**: Automatic approval bypass for vouchers below threshold; mandatory sign-off with enforced segregation of duties (author/payee cannot approve) for vouchers exceeding threshold.
- **Receipt Auditing**: Mandatory receipt attachment verification per expense category with deletion restrictions once submitted or posted.
- **Branch Top-up Replenishment**: Direct funding from authorized bank or treasury sources with non-expense/income source constraints and fiscal lock date verification.
- **Audit-Compliant Reversals**: Posted vouchers can only be cancelled via accounting reversing entries created in the fund's cash journal; posted top-ups are safely corrected through standard ledger reversal by accountants.
- **Multi-Company Isolation**: Complete data and ledger isolation across companies with clean dropdown behavior on newly initialized forms.

---

## 2. Security Model

| Role / Model | Fund (`petty.cash.fund`) | Category (`petty.cash.category`) | Voucher (`petty.cash.voucher`) | Top-up (`petty.cash.topup`) | Top-up Source (`petty.cash.topup.source`) | Accounting Group Needed |
|---|---|---|---|---|---|---|
| **Custodian** | Read-only (assigned funds) | Read-only | CRUD (assigned funds only) | Read-only (assigned funds) | No access | None |
| **Approver** | Read-only (assigned funds) | Read-only | CRUD + Approve / Reject | Read-only (assigned funds) | No access | None |
| **Manager (No Acct)** | Full CRUD, Activate, Close | Full CRUD | Full CRUD | Create / Read / Cancel Draft | Full CRUD | None |
| **Manager (+ Readonly)** | Full CRUD, can select journals | Full CRUD | Full CRUD | Create / Read / Cancel Draft | Full CRUD (can select accounts) | `account.group_account_readonly` |
| **Manager (+ Invoicing)** | Full CRUD, Activate, Close | Full CRUD | Full CRUD + Cancel (Reverse) | Full CRUD + Post to Ledger | Full CRUD | `account.group_account_invoice` |

---

## 3. Journal Entries Created

### A. Voucher Disbursement Entry (`account.move`, type `entry`)
Posted automatically when a voucher is approved or directly submitted below threshold:

| Line # | Account | Label | Partner | Debit | Credit |
|---|---|---|---|---|---|
| 1 | Expense Account (`category.expense_account_id`) | Voucher Description | Payee Partner / Employee Contact / None | Amount | 0.00 |
| 2 | Fund Cash Account (`fund.default_account_id`) | Voucher Description | Payee Partner / Employee Contact / None | 0.00 | Amount |

*Example (Voucher PCV/2026/00001 for 150.00 USD office supplies):*
- `Dr` 600095 Office Expenses: 150.00 USD
- `Cr` 101090 Petty Cash Liquidity: 150.00 USD

### B. Voucher Reversal Entry (`account.move`, type `entry`)
Created when a Manager cancels a posted voucher:

| Line # | Account | Label | Debit | Credit |
|---|---|---|---|---|
| 1 | Fund Cash Account (`fund.default_account_id`) | Reversal of: PCV/2026/00001 - <Reason> | Amount | 0.00 |
| 2 | Expense Account (`category.expense_account_id`) | Reversal of: PCV/2026/00001 - <Reason> | 0.00 | Amount |

### C. Fund Top-up Entry (`account.move`, type `entry`)
Created when a Manager posts a top-up:

| Line # | Account | Label | Debit | Credit |
|---|---|---|---|---|
| 1 | Fund Cash Account (`fund.default_account_id`) | Top-up from <Source Name> | Amount | 0.00 |
| 2 | Source Account (`source.account_id`) | Top-up from <Source Name> | 0.00 | Amount |

*Example (Top-up PCT/2026/00001 for 500.00 USD from Main Bank):*
- `Dr` 101090 Petty Cash Liquidity: 500.00 USD
- `Cr` 102000 Main Bank Account: 500.00 USD

---

## 4. Test Suite Coverage

| Test Class | Focus Area | Test Count |
|---|---|---|
| `TestPettyCashFoundation` | Fund creation, cash journal accounts, custodians, limits, live balance aggregation | 19 |
| `TestPettyCashVoucher` | Workflow state transitions, thresholds, approvals, segregation of duties, receipt validation, ledger posting, reversal, concurrency lock protection, UI Form tests | 26 |
| `TestPettyCashTopup` | Top-up creation, source validation, ledger posting, sequence no-gap continuity, fiscal lock dates, Manager/Custodian access controls, UI Form flows | 9 |
| **Total Tests** | | **54** |

---

## 5. Known Limitations
1. **Tax Engine**: Tax calculations and VAT breakdowns on petty cash vouchers are not supported.
2. **Physical Cash Counts**: Manual count reconciliations and cash drawer disparity tracking are out of scope.
3. **Dashboards & Custom Reports**: Dedicated QWeb analytical dashboards or executive PDF reports are not included.
4. **Single Currency**: Funds operate strictly in their journal's primary currency (no multi-currency voucher lines).
5. **Approval Segregation**: Setting an approval threshold mandates approval by a second authorized user (creator cannot approve own vouchers).
