# Petty Cash Management

**Version**: 20.0.1.0.0 | **License**: LGPL-3 | **Requires**: Odoo 20.0 Enterprise (`account_accountant`)

## Overview
Connects petty cash funds directly to Odoo Cash Journals (`account.journal` type `cash`). Fund balances are dynamically computed from posted journal items in real-time, eliminating shadow ledgers and balance drift.

## Roles & Accounting Prerequisites
- **Custodian (`group_petty_cash_custodian`)**: Operates assigned funds. Creates and submits expenses, resets own rejected expenses to draft; views own funds, categories, and own-fund top-ups. May edit non-control fields (name, notes) of assigned funds. Requires no accounting groups.
- **Manager (`group_petty_cash_manager`, implies Custodian)**: Full fund administration, categories, top-up sources, activating/closing funds, approving/rejecting expenses exceeding thresholds (segregation of duties applies).
  - *Selecting journals & source accounts*: Requires `account.group_account_readonly`.
  - *Posting top-ups & cancelling/reversing expenses*: Requires `account.group_account_invoice`.
  - *Modifying journal account definitions*: Requires `account.group_account_manager`.

## Branch Setup (Step-by-Step)
1. **Cash Journal**: Accounting > Configuration > Journals. Create a journal of type `Cash`. Set Default Liquidity Account (`asset_cash`), Suspense Account, and Outstanding Receipts / Payments accounts.
2. **Top-up Sources**: Petty Cash > Configuration > Top-up Sources. Create source pointing to a funding account (e.g. Bank or Treasury Cash; expense/income accounts prohibited).
3. **Expense Categories**: Petty Cash > Configuration > Categories. Define categories with expense accounts and receipt requirements.
4. **Petty Cash Fund**: Petty Cash > Funds. Create a fund, select the cash journal, and assign Custodian users. Controls (Low Balance Alert, Per-Expense Limit, and Approval Threshold) are configured at the company level under Settings > Petty Cash (or Petty Cash > Configuration > Settings for administrators).
5. **Activate**: Manager with accounting readonly clicks **Activate**.

## Daily Operations
- **Top-up**: Manager with Invoicing rights clicks **Top Up** on active fund (or `Petty Cash > Top-ups`), enters source and amount, then clicks **Post**. Generates balanced entry (`Dr` Fund Cash / `Cr` Source Account) and raises fund balance.
- **Expense Disbursement**: Custodian creates expense with date, category, amount, payee (partner, employee, other), and optional/mandatory receipt file (single receipt file per expense, required per category flag; no attachment override). Clicks **Submit**.
  - If amount <= company approval threshold: posts immediately to general ledger (`Dr` Expense Account / `Cr` Fund Cash).
  - If amount > company approval threshold: transitions to `submitted`. A different Manager reviews and clicks **Approve** (posts) or **Reject** (with mandatory reason).
  - If amount > company per-expense limit: submission is blocked.
- **Cancellation & Corrections**:
  - *Expenses*: Draft/rejected expenses can be cancelled directly. Posted expenses are cancelled via **Cancel** by a Manager holding Invoicing rights, which creates a formal reversing entry in the journal.
  - *Top-ups*: Draft top-ups can be cancelled. Posted top-ups cannot be cancelled or deleted directly; an accountant corrects mistakes by reversing the top-up entry in **Accounting > Journal Entries**.
  - *Accounting Synchronization*: If an accounting entry linked to an expense or top-up is reset to draft, cancelled, or reversed from Accounting, the linked expense/top-up is automatically set to `Cancelled` with an audit chatter note. If a reset entry is re-posted, the record is restored to `Posted` (with a non-blocking warning note if fund balance goes negative). Records cancelled with reversals never restore to posted.

## Scoped Sudo Calls (Exactly 3)
1. `models/petty_cash_fund.py`: line 168 — Read-only balance aggregation (`account.move.line.sudo()._read_group`) scoped strictly to the fund's cash journal and default account, as custodians do not hold direct read access to `account.move.line`.
2. `models/petty_cash_voucher.py`: line 535 — Creates and posts balanced cash disbursement ledger move upon expense submit/approval under fund company context without giving custodians broad invoicing rights.
3. `models/account_move.py`: line 33 — Entry-sync helper (`su_env = self.sudo().env`) allowing accountants lacking petty cash groups to synchronize linked voucher/top-up workflow status and post audit chatter notes when journal entries are reset to draft, cancelled, reversed, or re-posted.

## Known Limitations
- **Taxes**: Tax computation on expenses is not supported in this release.
- **Cash Counts**: Physical cash count reconciliation is not included.
- **Reporting**: Dedicated petty cash dashboards or custom PDF reports are not included.
- **Currency**: Single-currency per fund (matches journal/company currency).
- **Thresholds**: 0 disables approval requirement or per-expense limit; company settings apply to all funds of that company. Setting an approval threshold enforces strict segregation of duties (author/payee cannot approve).
