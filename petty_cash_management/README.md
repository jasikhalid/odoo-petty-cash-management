# Petty Cash Management

**Version**: 20.0.1.0.0 | **License**: LGPL-3 | **Requires**: Odoo 20.0 Enterprise (`account_accountant`)

## Overview
Connects petty cash funds directly to Odoo Cash Journals (`account.journal` type `cash`). Fund balances are dynamically computed from posted journal items in real-time, eliminating shadow ledgers and balance drift.

## Roles & Accounting Prerequisites
- **Custodian (`group_petty_cash_custodian`)**: Operates assigned funds. Creates and submits vouchers; views own funds and top-ups. Requires no accounting groups.
- **Approver (`group_petty_cash_approver`)**: Approves vouchers exceeding fund thresholds (requires 2 distinct people). Bound by custodian assignment.
- **Manager (`group_petty_cash_manager`)**: Full fund administration, categories, top-up sources, activating/closing funds.
  - *Selecting journals & source accounts*: Requires `account.group_account_readonly`.
  - *Posting top-ups & cancelling/reversing vouchers*: Requires `account.group_account_invoice`.
  - *Modifying journal account definitions*: Requires `account.group_account_manager`.

## Branch Setup (Step-by-Step)
1. **Cash Journal**: Accounting > Configuration > Journals. Create a journal of type `Cash`. Set Default Liquidity Account (`asset_cash`), Suspense Account, and Outstanding Receipts / Payments accounts.
2. **Top-up Sources**: Petty Cash > Configuration > Top-up Sources. Create source pointing to a funding account (e.g. Bank or Treasury Cash; expense/income accounts prohibited).
3. **Expense Categories**: Petty Cash > Configuration > Categories. Define categories with expense accounts and receipt requirements.
4. **Petty Cash Fund**: Petty Cash > Funds. Create a fund, select the cash journal, assign Custodian users, optional analytic account, per-voucher limit, and approval threshold.
5. **Activate**: Manager with accounting readonly clicks **Activate**.

## Daily Operations
- **Top-up**: Manager with Invoicing rights clicks **Top Up** on active fund (or `Petty Cash > Top-ups`), enters source and amount, then clicks **Post**. Generates balanced entry (`Dr` Fund Cash / `Cr` Source Account) and raises fund balance.
- **Voucher Disbursement**: Custodian creates voucher with date, category, amount, payee (partner, employee, other), and receipt attachments (required per category flag only). Clicks **Submit**.
  - If amount <= threshold: posts immediately to general ledger (`Dr` Expense Account / `Cr` Fund Cash).
  - If amount > threshold: transitions to `submitted`. A different Approver/Manager reviews and clicks **Approve** (posts) or **Reject** (with mandatory reason).
- **Cancellation & Corrections**:
  - *Vouchers*: Draft/rejected vouchers can be cancelled directly. Posted vouchers are cancelled via **Cancel** by a Manager holding Invoicing rights, which creates a formal reversing entry in the journal.
  - *Top-ups*: Draft top-ups can be cancelled. Posted top-ups cannot be cancelled or deleted; an accountant corrects mistakes by reversing the top-up entry in **Accounting > Journal Entries**.

## Known Limitations
- **Taxes**: Tax computation on vouchers is not supported in this release.
- **Cash Counts**: Physical cash count reconciliation is not included.
- **Reporting**: Dedicated petty cash dashboards or custom PDF reports are not included.
- **Currency**: Single-currency per fund (matches journal/company currency).
- **Thresholds**: Blank approval threshold disables approval requirement; non-blank threshold enforces strict segregation of duties (author/payee cannot approve).
