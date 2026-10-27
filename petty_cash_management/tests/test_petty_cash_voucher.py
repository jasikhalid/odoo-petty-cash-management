from datetime import date, timedelta
from unittest.mock import patch
import psycopg2

from odoo import Command, fields
from odoo.exceptions import AccessError, LockError, UserError, ValidationError
from odoo.tests import Form, tagged, new_test_user
from odoo.tools.safe_eval import safe_eval
from odoo.tools.misc import formatLang
from odoo.tools import mute_logger
from odoo.addons.account.tests.common import AccountTestInvoicingCommon


@tagged('post_install', '-at_install')
class TestPettyCashVoucher(AccountTestInvoicingCommon):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()

        # Grant Petty Cash Manager and HR Officer groups to test runner user
        cls.env.user.write({
            'group_ids': [
                Command.link(cls.env.ref('petty_cash_management.group_petty_cash_manager').id),
                Command.link(cls.env.ref('hr.group_hr_user').id),
            ],
        })

        cls.company = cls.env.company
        cls.currency = cls.company.currency_id


        # Accounts for cash journal
        cls.petty_cash_account = cls.env['account.account'].create({
            'name': 'Petty Cash Liquidity',
            'code': '101090',
            'account_type': 'asset_cash',
            'company_ids': [Command.link(cls.company.id)],
        })
        cls.suspense_account = cls.env['account.account'].create({
            'name': 'Petty Cash Suspense',
            'code': '101091',
            'account_type': 'asset_current',
            'company_ids': [Command.link(cls.company.id)],
        })
        cls.outstanding_receipts_account = cls.env['account.account'].create({
            'name': 'Petty Cash Out Receipts',
            'code': '101092',
            'account_type': 'asset_current',
            'company_ids': [Command.link(cls.company.id)],
        })
        cls.outstanding_payments_account = cls.env['account.account'].create({
            'name': 'Petty Cash Out Payments',
            'code': '101093',
            'account_type': 'asset_current',
            'company_ids': [Command.link(cls.company.id)],
        })

        # Cash journal
        cls.journal = cls.env['account.journal'].create({
            'name': 'Petty Cash Journal',
            'code': 'PCV1',
            'type': 'cash',
            'company_id': cls.company.id,
            'default_account_id': cls.petty_cash_account.id,
            'suspense_account_id': cls.suspense_account.id,
            'inbound_payment_method_line_ids': [
                Command.create({
                    'payment_method_id': cls.env.ref('account.account_payment_method_manual_in').id,
                    'payment_account_id': cls.outstanding_receipts_account.id,
                }),
            ],
            'outbound_payment_method_line_ids': [
                Command.create({
                    'payment_method_id': cls.env.ref('account.account_payment_method_manual_out').id,
                    'payment_account_id': cls.outstanding_payments_account.id,
                }),
            ],
        })

        # Expense account & Category
        cls.expense_account = cls.env['account.account'].create({
            'name': 'General Office Expense',
            'code': '600090',
            'account_type': 'expense',
            'company_ids': [Command.link(cls.company.id)],
        })
        cls.category = cls.env['petty.cash.category'].create({
            'name': 'Office Supplies',
            'expense_account_id': cls.expense_account.id,
            'company_id': cls.company.id,
            'receipt_required': False,
        })
        cls.category_receipt_req = cls.env['petty.cash.category'].create({
            'name': 'Travel with Receipt',
            'expense_account_id': cls.expense_account.id,
            'company_id': cls.company.id,
            'receipt_required': True,
        })

        # Users
        cls.custodian_user = new_test_user(
            cls.env,
            login='voucher_custodian',
            name='Voucher Custodian',
            groups='petty_cash_management.group_petty_cash_custodian',
            company_id=cls.company.id,
            company_ids=[Command.set(cls.company.ids)],
        )
        cls.custodian_user_2 = new_test_user(
            cls.env,
            login='voucher_custodian_2',
            name='Voucher Custodian 2',
            groups='petty_cash_management.group_petty_cash_custodian',
            company_id=cls.company.id,
            company_ids=[Command.set(cls.company.ids)],
        )
        cls.approver_user = new_test_user(
            cls.env,
            login='voucher_approver',
            name='Voucher Approver',
            groups='petty_cash_management.group_petty_cash_approver',
            company_id=cls.company.id,
            company_ids=[Command.set(cls.company.ids)],
        )
        cls.manager_user = new_test_user(
            cls.env,
            login='voucher_manager',
            name='Voucher Manager',
            groups='petty_cash_management.group_petty_cash_manager,account.group_account_invoice',
            company_id=cls.company.id,
            company_ids=[Command.set(cls.company.ids)],
        )
        cls.manager_readonly_user = new_test_user(
            cls.env,
            login='voucher_manager_readonly',
            name='Voucher Manager Readonly',
            groups='petty_cash_management.group_petty_cash_manager,account.group_account_readonly',
            company_id=cls.company.id,
            company_ids=[Command.set(cls.company.ids)],
        )

        # Fund
        cls.fund = cls.env['petty.cash.fund'].create({
            'name': 'Main Branch Fund',
            'journal_id': cls.journal.id,
            'custodian_ids': [Command.set([cls.custodian_user.id, cls.approver_user.id])],
            'company_id': cls.company.id,
            'approval_threshold': 100.0,
            'per_voucher_limit': 500.0,
        })
        cls.fund.action_activate()

        # Partner & Employee
        cls.partner = cls.env['res.partner'].create({
            'name': 'Stationery Vendor',
            'company_id': cls.company.id,
        })
        cls.employee_user = new_test_user(
            cls.env,
            login='emp_user',
            name='Employee User',
            groups='base.group_user',
            company_id=cls.company.id,
            company_ids=[Command.set(cls.company.ids)],
        )
        cls.employee = cls.env['hr.employee'].create({
            'name': 'Reception Staff',
            'user_id': cls.employee_user.id,
            'company_id': cls.company.id,
        })

        # Inject initial cash balance into fund (e.g. 1000.0)
        bank_acc = cls.company_data['default_journal_bank'].default_account_id
        topup_move = cls.env['account.move'].create({
            'journal_id': cls.journal.id,
            'date': fields.Date.context_today(cls.env.user),
            'line_ids': [
                Command.create({
                    'account_id': cls.petty_cash_account.id,
                    'debit': 1000.0,
                    'credit': 0.0,
                }),
                Command.create({
                    'account_id': bank_acc.id,
                    'debit': 0.0,
                    'credit': 1000.0,
                }),
            ],
        })
        topup_move.action_post()
        cls.fund.invalidate_recordset(['balance'])

    # -------------------------------------------------------------------------
    # TESTS
    # -------------------------------------------------------------------------

    def test_01_below_threshold_submit_posts_immediately(self):
        """Below approval threshold: submit posts immediately, ledger lines balanced, balance drops."""
        initial_balance = self.fund.balance
        voucher = self.env['petty.cash.voucher'].with_user(self.custodian_user).create({
            'fund_id': self.fund.id,
            'category_id': self.category.id,
            'description': 'Office Pens and Paper',
            'amount': 50.0,
            'payee_type': 'partner',
            'partner_id': self.partner.id,
        })
        self.assertEqual(voucher.state, 'draft')
        self.assertEqual(voucher.name, 'New')

        voucher.action_submit()

        self.assertEqual(voucher.state, 'posted')
        self.assertTrue(voucher.name.startswith('PCV/'))
        self.assertTrue(voucher.move_id)
        self.assertEqual(voucher.move_id.state, 'posted')

        # Assert exact ledger lines (inspected with test environment since custodian has no move.line read access)
        lines = voucher.move_id.with_env(self.env).line_ids
        self.assertEqual(len(lines), 2)
        exp_line = lines.filtered(lambda l: l.account_id == self.expense_account)
        cash_line = lines.filtered(lambda l: l.account_id == self.petty_cash_account)

        self.assertEqual(exp_line.debit, 50.0)
        self.assertEqual(exp_line.credit, 0.0)
        self.assertEqual(exp_line.partner_id, self.partner)
        self.assertEqual(exp_line.name, 'Office Pens and Paper')

        self.assertEqual(cash_line.debit, 0.0)
        self.assertEqual(cash_line.credit, 50.0)
        self.assertEqual(cash_line.partner_id, self.partner)

        self.fund.invalidate_recordset(['balance'])
        self.assertEqual(self.fund.balance, initial_balance - 50.0)

    def test_02_above_threshold_requires_approver(self):
        """Above threshold: stays submitted, custodian cannot approve, approver approves -> posted."""
        voucher = self.env['petty.cash.voucher'].with_user(self.custodian_user).create({
            'fund_id': self.fund.id,
            'category_id': self.category.id,
            'description': 'Bulk Desk Organizers',
            'amount': 150.0,
            'payee_type': 'partner',
            'partner_id': self.partner.id,
        })
        voucher.action_submit()
        self.assertEqual(voucher.state, 'submitted')

        # Custodian cannot approve
        with self.assertRaises(UserError):
            voucher.with_user(self.custodian_user).action_approve()

        # Approver approves
        voucher.with_user(self.approver_user).action_approve()
        self.assertEqual(voucher.state, 'posted')
        self.assertEqual(voucher.approved_by_id, self.approver_user)
        self.assertTrue(voucher.approved_date)

    def test_03_segregation_of_duties_creator_and_payee_cannot_approve(self):
        """Creator cannot approve own voucher; employee payee cannot approve own voucher."""
        # 1. Approver creates a voucher above threshold
        voucher = self.env['petty.cash.voucher'].with_user(self.approver_user).create({
            'fund_id': self.fund.id,
            'category_id': self.category.id,
            'description': 'Approver Created Voucher',
            'amount': 120.0,
            'payee_type': 'partner',
            'partner_id': self.partner.id,
        })
        voucher.action_submit()
        self.assertEqual(voucher.state, 'submitted')

        # Approver tries to approve their own voucher: BLOCKED
        with self.assertRaises(UserError) as cm:
            voucher.with_user(self.approver_user).action_approve()
        self.assertIn("Segregation of duties", str(cm.exception))

        # 2. Voucher where payee employee is linked to the user
        self.fund.write({'custodian_ids': [Command.link(self.employee_user.id)]})
        # Grant approver rights to employee_user
        self.employee_user.write({'group_ids': [Command.link(self.env.ref('petty_cash_management.group_petty_cash_approver').id)]})

        voucher2 = self.env['petty.cash.voucher'].with_user(self.custodian_user).create({
            'fund_id': self.fund.id,
            'category_id': self.category.id,
            'description': 'Reimbursement for Employee',
            'amount': 130.0,
            'payee_type': 'employee',
            'employee_id': self.employee.id,
        })
        voucher2.action_submit()

        # Employee user tries to approve: BLOCKED
        with self.assertRaises(UserError) as cm:
            voucher2.with_user(self.employee_user).action_approve()
        self.assertIn("Segregation of duties", str(cm.exception))

    def test_04_reject_reset_to_draft_resubmit(self):
        """Reject with reason wizard -> reset to draft (only from rejected) -> resubmit."""
        voucher = self.env['petty.cash.voucher'].with_user(self.custodian_user).create({
            'fund_id': self.fund.id,
            'category_id': self.category.id,
            'description': 'Coffee Machine Repair',
            'amount': 150.0,
            'payee_type': 'partner',
            'partner_id': self.partner.id,
        })
        voucher.action_submit()
        self.assertEqual(voucher.state, 'submitted')

        # Cannot reset to draft from submitted
        with self.assertRaises(UserError):
            voucher.with_user(self.custodian_user).action_reset_to_draft()

        # Approver rejects with reason
        wizard = self.env['petty.cash.voucher.reason.wizard'].with_user(self.approver_user).create({
            'voucher_id': voucher.id,
            'action_type': 'reject',
            'reason': 'Invoice quotation missing breakdown.',
        })
        wizard.action_confirm()

        self.assertEqual(voucher.state, 'rejected')
        self.assertEqual(voucher.rejection_reason, 'Invoice quotation missing breakdown.')

        # Creator resets to draft
        voucher.with_user(self.custodian_user).action_reset_to_draft()
        self.assertEqual(voucher.state, 'draft')

        # Edit amount below threshold and resubmit -> posts
        voucher.with_user(self.custodian_user).write({'amount': 80.0})
        voucher.with_user(self.custodian_user).action_submit()
        self.assertEqual(voucher.state, 'posted')

    def test_05_receipt_rules_and_per_voucher_limit(self):
        """Receipt required category requires attachment; other payee without receipt requirement submits cleanly; per_voucher_limit hard stop."""
        expected_msg = f"A receipt attachment is required for category '{self.category_receipt_req.name}'. Attach the receipt, or ask a Manager to untick 'Receipt Required' on the category."

        # 1. Partner payee, category receipt_required=True, no attachment: blocked
        voucher = self.env['petty.cash.voucher'].with_user(self.custodian_user).create({
            'fund_id': self.fund.id,
            'category_id': self.category_receipt_req.id,
            'description': 'Taxi ride without receipt',
            'amount': 40.0,
            'payee_type': 'partner',
            'partner_id': self.partner.id,
        })
        with self.assertRaises(UserError) as cm:
            voucher.action_submit()
        self.assertEqual(str(cm.exception), expected_msg)

        # Add attachment -> submit succeeds
        self.env['ir.attachment'].create({
            'name': 'taxi_bill.pdf',
            'res_model': 'petty.cash.voucher',
            'res_id': voucher.id,
            'raw': b'fake receipt content',
        })
        voucher.invalidate_recordset(['attachment_count'])
        voucher.action_submit()
        self.assertEqual(voucher.state, 'posted')

        # 2. Free-text "other" payee, category receipt_required=True, no attachment: blocked with new message
        voucher_other_req = self.env['petty.cash.voucher'].with_user(self.custodian_user).create({
            'fund_id': self.fund.id,
            'category_id': self.category_receipt_req.id,
            'description': 'Local Plumber with Receipt Required',
            'amount': 30.0,
            'payee_type': 'other',
            'payee_name': 'Ramesh Kumar',
        })
        with self.assertRaises(UserError) as cm:
            voucher_other_req.action_submit()
        self.assertEqual(str(cm.exception), expected_msg)

        # 3. Free-text "other" payee, category receipt_required=False, no attachment: submits and posts
        voucher_other_noreq = self.env['petty.cash.voucher'].with_user(self.custodian_user).create({
            'fund_id': self.fund.id,
            'category_id': self.category.id,  # receipt_required=False
            'description': 'Local Plumber without Receipt Required',
            'amount': 25.0,
            'payee_type': 'other',
            'payee_name': 'Ramesh Kumar',
        })
        voucher_other_noreq.action_submit()
        self.assertEqual(voucher_other_noreq.state, 'posted')

        # 4. Per voucher limit hard stop
        voucher_high = self.env['petty.cash.voucher'].with_user(self.custodian_user).create({
            'fund_id': self.fund.id,
            'category_id': self.category.id,
            'description': 'Too expensive item',
            'amount': 600.0,  # limit is 500.0
            'payee_type': 'partner',
            'partner_id': self.partner.id,
        })
        with self.assertRaises(UserError) as cm:
            voucher_high.action_submit()
        self.assertIn("exceeds the fund's per-voucher limit", str(cm.exception))

    def test_06_payee_types_and_lines_formatting(self):
        """Payee types: partner, employee (work_contact_id), other (no partner on lines, name in label)."""
        # (a) Partner
        v_partner = self.env['petty.cash.voucher'].with_user(self.custodian_user).create({
            'fund_id': self.fund.id,
            'category_id': self.category.id,
            'description': 'Partner Payee',
            'amount': 20.0,
            'payee_type': 'partner',
            'partner_id': self.partner.id,
        })
        v_partner.action_submit()
        self.assertEqual(v_partner.move_id.with_env(self.env).line_ids[0].partner_id, self.partner)

        # (b) Employee
        v_emp = self.env['petty.cash.voucher'].with_user(self.custodian_user).create({
            'fund_id': self.fund.id,
            'category_id': self.category.id,
            'description': 'Employee Reimbursement',
            'amount': 25.0,
            'payee_type': 'employee',
            'employee_id': self.employee.id,
        })
        v_emp.action_submit()
        self.assertEqual(v_emp.move_id.with_env(self.env).line_ids[0].partner_id, self.employee.work_contact_id)

        # Employee without work contact is blocked
        emp_no_wc = self.env['hr.employee'].create({'name': 'Employee No Contact'})
        emp_no_wc.work_contact_id = False
        v_emp_bad = self.env['petty.cash.voucher'].with_user(self.custodian_user).create({
            'fund_id': self.fund.id,
            'category_id': self.category.id,
            'description': 'Broken Employee',
            'amount': 15.0,
            'payee_type': 'employee',
            'employee_id': emp_no_wc.id,
        })
        with self.assertRaises(UserError) as cm:
            v_emp_bad.action_submit()
        self.assertIn("no work contact partner configured", str(cm.exception))

        # (c) Other / free text
        v_other = self.env['petty.cash.voucher'].with_user(self.custodian_user).create({
            'fund_id': self.fund.id,
            'category_id': self.category.id,
            'description': 'Courier delivery',
            'amount': 10.0,
            'payee_type': 'other',
            'payee_name': 'Express Couriers',
        })
        # Add receipt
        self.env['ir.attachment'].create({
            'name': 'courier_slip.jpg',
            'res_model': 'petty.cash.voucher',
            'res_id': v_other.id,
            'raw': b'slip image',
        })
        v_other.invalidate_recordset(['attachment_count'])
        v_other.action_submit()

        # Assert no partner on lines, name in label
        lines = v_other.move_id.with_env(self.env).line_ids
        self.assertFalse(lines[0].partner_id)
        self.assertFalse(lines[1].partner_id)
        self.assertIn("Express Couriers", lines[1].name)

    def test_07_no_negative_cash_and_sequential_overdraft_prevention(self):
        """Refuse posting if fund balance is insufficient."""
        self.fund.invalidate_recordset(['balance'])
        cur_balance = self.fund.balance

        # Voucher exceeding available balance
        voucher_big = self.env['petty.cash.voucher'].with_user(self.custodian_user).create({
            'fund_id': self.fund.id,
            'category_id': self.category.id,
            'description': 'Gigantic Payout',
            'amount': cur_balance + 10.0,
            'payee_type': 'partner',
            'partner_id': self.partner.id,
        })
        # Remove per_voucher_limit and approval_threshold for this test so it posts on submit
        self.fund.write({'per_voucher_limit': 0.0, 'approval_threshold': 0.0})

        with self.assertRaises(UserError) as cm:
            voucher_big.action_submit()
        self.assertIn("Insufficient funds", str(cm.exception))

    def test_08_category_with_taxes_blocked(self):
        """A category with taxes blocks submit with a clear error."""
        tax = self.env['account.tax'].create({
            'name': 'GST 18%',
            'amount': 18.0,
            'amount_type': 'percent',
            'type_tax_use': 'purchase',
            'company_id': self.company.id,
        })
        cat_taxed = self.env['petty.cash.category'].create({
            'name': 'Taxed Supplies',
            'expense_account_id': self.expense_account.id,
            'company_id': self.company.id,
            'tax_ids': [Command.link(tax.id)],
        })
        voucher = self.env['petty.cash.voucher'].with_user(self.custodian_user).create({
            'fund_id': self.fund.id,
            'category_id': cat_taxed.id,
            'description': 'Taxed Item',
            'amount': 50.0,
            'payee_type': 'partner',
            'partner_id': self.partner.id,
        })
        with self.assertRaises(UserError) as cm:
            voucher.action_submit()
        self.assertIn("Taxes are not supported in Phase 2", str(cm.exception))

    def test_09_custodian_only_user_submits_without_accounting_rights(self):
        """Custodian-only user completes submit->posted without AccessError; move creator is real user."""
        self.assertFalse(self.custodian_user.has_group('account.group_account_invoice'))
        self.assertFalse(self.custodian_user.has_group('account.group_account_readonly'))

        voucher = self.env['petty.cash.voucher'].with_user(self.custodian_user).create({
            'fund_id': self.fund.id,
            'category_id': self.category.id,
            'description': 'Custodian Post Test',
            'amount': 35.0,
            'payee_type': 'partner',
            'partner_id': self.partner.id,
        })
        voucher.action_submit()

        self.assertEqual(voucher.state, 'posted')
        self.assertTrue(voucher.move_id)
        # Entry creator in DB is the real custodian user, NOT OdooBot
        self.assertEqual(voucher.move_id.create_uid, self.custodian_user)

        # Custodian still cannot directly read account.move.line
        with self.assertRaises(AccessError):
            self.env['account.move.line'].with_user(self.custodian_user).search([])

    def test_10_posting_into_locked_period_fails_for_custodian(self):
        """Posting into locked fiscal period fails with UserError even under sudo."""
        lock_date = date.today() - timedelta(days=5)
        self.company.write({'fiscalyear_lock_date': lock_date})

        voucher = self.env['petty.cash.voucher'].with_user(self.custodian_user).create({
            'fund_id': self.fund.id,
            'category_id': self.category.id,
            'description': 'Locked Period Expense',
            'date': lock_date - timedelta(days=2),
            'amount': 25.0,
            'payee_type': 'partner',
            'partner_id': self.partner.id,
        })
        with self.assertRaises(UserError) as cm:
            voucher.action_submit()
        self.assertIn("You cannot add/modify entries prior to and inclusive of", str(cm.exception))

    def test_11_manager_reversal_and_accounting_group_restrictions(self):
        """Manager with only account_readonly cannot reverse; Manager with account_invoice can reverse."""
        voucher = self.env['petty.cash.voucher'].with_user(self.custodian_user).create({
            'fund_id': self.fund.id,
            'category_id': self.category.id,
            'description': 'Voucher to Reverse',
            'amount': 45.0,
            'payee_type': 'partner',
            'partner_id': self.partner.id,
        })
        voucher.action_submit()
        self.assertEqual(voucher.state, 'posted')
        self.fund.invalidate_recordset(['balance'])
        bal_after_post = self.fund.balance

        # 1. Custodian cannot cancel/reverse posted voucher
        with self.assertRaises(UserError):
            voucher.with_user(self.custodian_user).action_cancel()

        # 2. Manager with only account.group_account_readonly cannot reverse (no sudo)
        with self.assertRaises(UserError) as cm:
            wizard_ro = self.env['petty.cash.voucher.reason.wizard'].with_user(self.manager_readonly_user).create({
                'voucher_id': voucher.id,
                'action_type': 'cancel',
                'reason': 'Accidental duplicate',
            })
            wizard_ro.action_confirm()
        self.assertIn("requires the Invoicing/Billing accounting group", str(cm.exception))

        # 3. Manager with account.group_account_invoice can reverse
        wizard_mgr = self.env['petty.cash.voucher.reason.wizard'].with_user(self.manager_user).create({
            'voucher_id': voucher.id,
            'action_type': 'cancel',
            'reason': 'Returned unused item to vendor',
        })
        wizard_mgr.action_confirm()

        self.assertEqual(voucher.state, 'cancelled')
        self.assertTrue(voucher.reversal_move_id)
        self.assertEqual(voucher.reversal_move_id.state, 'posted')

        # Balance is restored
        self.fund.invalidate_recordset(['balance'])
        self.assertEqual(self.fund.balance, bal_after_post + 45.0)

    def test_12_posted_voucher_immutability(self):
        """Posted voucher fields cannot be edited and voucher cannot be deleted."""
        voucher = self.env['petty.cash.voucher'].with_user(self.custodian_user).create({
            'fund_id': self.fund.id,
            'category_id': self.category.id,
            'description': 'Immutable Voucher',
            'amount': 30.0,
            'payee_type': 'partner',
            'partner_id': self.partner.id,
        })
        voucher.action_submit()
        self.assertEqual(voucher.state, 'posted')

        with self.assertRaises(UserError):
            voucher.write({'amount': 99.0})
        with self.assertRaises(UserError):
            voucher.write({'description': 'Hacked description'})
        with self.assertRaises(UserError):
            voucher.unlink()

        # Calling _post_to_ledger twice raises UserError
        with self.assertRaises(UserError):
            voucher._post_to_ledger()

    def test_13_record_rules_isolation_custodian_and_multi_company(self):
        """Custodian only sees vouchers of own funds; Manager sees all; multi-company isolation."""
        journal_2 = self.env['account.journal'].create({
            'name': 'Cash 2',
            'code': 'PC22',
            'type': 'cash',
            'company_id': self.company.id,
            'default_account_id': self.petty_cash_account.id,
            'suspense_account_id': self.suspense_account.id,
            'inbound_payment_method_line_ids': [
                Command.create({
                    'payment_method_id': self.env.ref('account.account_payment_method_manual_in').id,
                    'payment_account_id': self.outstanding_receipts_account.id,
                }),
            ],
            'outbound_payment_method_line_ids': [
                Command.create({
                    'payment_method_id': self.env.ref('account.account_payment_method_manual_out').id,
                    'payment_account_id': self.outstanding_payments_account.id,
                }),
            ],
        })
        fund_2 = self.env['petty.cash.fund'].create({
            'name': 'Second Branch Fund',
            'journal_id': journal_2.id,
            'custodian_ids': [Command.set([self.custodian_user_2.id])],
            'company_id': self.company.id,
        })
        fund_2.action_activate()

        # Give fund_2 initial balance
        topup2 = self.env['account.move'].create({
            'journal_id': fund_2.journal_id.id,
            'line_ids': [
                Command.create({'account_id': self.petty_cash_account.id, 'debit': 500.0}),
                Command.create({'account_id': self.suspense_account.id, 'credit': 500.0}),
            ],
        })
        topup2.action_post()

        v1 = self.env['petty.cash.voucher'].with_user(self.custodian_user).create({
            'fund_id': self.fund.id,
            'category_id': self.category.id,
            'description': 'Voucher Fund 1',
            'amount': 20.0,
            'payee_type': 'partner',
            'partner_id': self.partner.id,
        })
        v2 = self.env['petty.cash.voucher'].with_user(self.custodian_user_2).create({
            'fund_id': fund_2.id,
            'category_id': self.category.id,
            'description': 'Voucher Fund 2',
            'amount': 20.0,
            'payee_type': 'partner',
            'partner_id': self.partner.id,
        })

        # Custodian 1 sees v1, but cannot see v2
        c1_vouchers = self.env['petty.cash.voucher'].with_user(self.custodian_user).search([])
        self.assertIn(v1, c1_vouchers)
        self.assertNotIn(v2, c1_vouchers)

        # Manager sees both
        mgr_vouchers = self.env['petty.cash.voucher'].with_user(self.manager_user).search([])
        self.assertIn(v1, mgr_vouchers)
        self.assertIn(v2, mgr_vouchers)

    def test_14_fund_close_blocked_by_open_vouchers(self):
        """Fund cannot be closed while it has draft or submitted vouchers."""
        voucher = self.env['petty.cash.voucher'].with_user(self.custodian_user).create({
            'fund_id': self.fund.id,
            'category_id': self.category.id,
            'description': 'Unprocessed Voucher',
            'amount': 15.0,
            'payee_type': 'partner',
            'partner_id': self.partner.id,
        })
        with self.assertRaises(UserError) as cm:
            self.fund.with_user(self.manager_user).action_close()
        self.assertIn("Cannot close a fund with draft or submitted vouchers", str(cm.exception))

    def test_15_receipt_attachments_immutability(self):
        """Attachments on submitted or posted vouchers cannot be deleted or modified."""
        voucher = self.env['petty.cash.voucher'].with_user(self.custodian_user).create({
            'fund_id': self.fund.id,
            'category_id': self.category_receipt_req.id,
            'description': 'Audited Expense',
            'amount': 50.0,
            'payee_type': 'partner',
            'partner_id': self.partner.id,
        })
        attachment = self.env['ir.attachment'].create({
            'name': 'receipt.png',
            'res_model': 'petty.cash.voucher',
            'res_id': voucher.id,
            'raw': b'receipt content',
        })
        voucher.action_submit()
        self.assertEqual(voucher.state, 'posted')

        # Attempt to unlink attachment: BLOCKED
        with self.assertRaises(UserError) as cm:
            attachment.unlink()
        self.assertIn("Receipt attachments cannot be modified or deleted", str(cm.exception))

        # Attempt to replace attachment binary content: BLOCKED
        with self.assertRaises(UserError) as cm:
            attachment.write({'raw': b'tampered content'})
        self.assertIn("Receipt attachments cannot be modified or deleted", str(cm.exception))

        # Attempt to move attachment to another voucher: BLOCKED
        voucher_other = self.env['petty.cash.voucher'].with_user(self.custodian_user).create({
            'fund_id': self.fund.id,
            'category_id': self.category.id,
            'description': 'Other Voucher',
            'amount': 10.0,
            'payee_type': 'partner',
            'partner_id': self.partner.id,
        })
        with self.assertRaises(UserError) as cm:
            attachment.write({'res_id': voucher_other.id})
        self.assertIn("Receipt attachments cannot be modified or deleted", str(cm.exception))

        # Adding a NEW attachment to a posted voucher is ALLOWED
        new_att = self.env['ir.attachment'].create({
            'name': 'additional_note.pdf',
            'res_model': 'petty.cash.voucher',
            'res_id': voucher.id,
            'raw': b'additional notes',
        })
        self.assertTrue(new_att.id)

    def test_16_custodian_only_user_creates_and_submits_employee_voucher(self):
        """Custodian-only user (no HR rights) picks hr.employee via name_search, reads work contact, and submits."""
        self.assertFalse(self.custodian_user.has_group('hr.group_hr_user'))
        self.assertFalse(self.custodian_user.has_group('account.group_account_invoice'))

        # Name search hr.employee as custodian
        matches = self.env['hr.employee'].with_user(self.custodian_user).name_search('Reception Staff')
        self.assertTrue(matches)
        emp_id = matches[0][0]

        voucher = self.env['petty.cash.voucher'].with_user(self.custodian_user).create({
            'fund_id': self.fund.id,
            'category_id': self.category.id,
            'description': 'Reception Staff Travel',
            'amount': 22.0,
            'payee_type': 'employee',
            'employee_id': emp_id,
        })
        voucher.action_submit()
        self.assertEqual(voucher.state, 'posted')
        self.assertEqual(voucher.move_id.with_env(self.env).line_ids[0].partner_id, self.employee.work_contact_id)

    def test_17_concurrency_lock_error_conversion(self):
        """Simulate lock contention: LockError is converted into a clear UserError."""
        voucher = self.env['petty.cash.voucher'].with_user(self.custodian_user).create({
            'fund_id': self.fund.id,
            'category_id': self.category.id,
            'description': 'Concurrent Voucher',
            'amount': 30.0,
            'payee_type': 'partner',
            'partner_id': self.partner.id,
        })
        # Mock fund.lock_for_update to raise LockError
        with patch.object(type(self.fund), 'lock_for_update', side_effect=LockError("Locked by another txn")):
            with self.assertRaises(UserError) as cm:
                voucher.action_submit()
            self.assertIn("The fund is being updated by someone else, please try again", str(cm.exception))

    def test_18_server_side_workflow_write_protection(self):
        """Workflow fields cannot be written or set via create directly through ORM/RPC."""
        voucher = self.env['petty.cash.voucher'].with_user(self.custodian_user).create({
            'fund_id': self.fund.id,
            'category_id': self.category.id,
            'description': 'Workflow Security Test',
            'amount': 20.0,
            'payee_type': 'partner',
            'partner_id': self.partner.id,
        })
        dummy_move = self.env['account.move'].search([], limit=1)

        # 1. Test as Custodian-only user
        for field, bad_val in [
            ('state', 'posted'),
            ('state', 'submitted'),
            ('name', 'PCV/2026/99999'),
            ('move_id', dummy_move.id),
            ('reversal_move_id', dummy_move.id),
            ('submitted_by_id', self.custodian_user.id),
            ('submitted_date', fields.Datetime.now()),
            ('approved_by_id', self.approver_user.id),
            ('approved_date', fields.Datetime.now()),
            ('rejection_reason', 'Unauthorized edit'),
            ('cancel_reason', 'Unauthorized edit'),
        ]:
            with self.assertRaises(UserError, msg=f"Writing {field} directly should be blocked for custodian"):
                voucher.with_user(self.custodian_user).write({field: bad_val})

        # 2. Test as Manager
        for field, bad_val in [
            ('state', 'posted'),
            ('state', 'submitted'),
            ('name', 'PCV/2026/99999'),
            ('move_id', dummy_move.id),
            ('reversal_move_id', dummy_move.id),
            ('submitted_by_id', self.custodian_user.id),
            ('submitted_date', fields.Datetime.now()),
            ('approved_by_id', self.approver_user.id),
            ('approved_date', fields.Datetime.now()),
            ('rejection_reason', 'Unauthorized edit'),
            ('cancel_reason', 'Unauthorized edit'),
        ]:
            with self.assertRaises(UserError, msg=f"Writing {field} directly should be blocked for manager"):
                voucher.with_user(self.manager_user).write({field: bad_val})

        # 3. Create cannot specify state != draft or name != New or other workflow fields
        with self.assertRaises(UserError):
            self.env['petty.cash.voucher'].with_user(self.custodian_user).create({
                'fund_id': self.fund.id,
                'category_id': self.category.id,
                'description': 'Direct Post Create',
                'amount': 15.0,
                'payee_type': 'partner',
                'partner_id': self.partner.id,
                'state': 'posted',
            })

        with self.assertRaises(UserError):
            self.env['petty.cash.voucher'].with_user(self.custodian_user).create({
                'fund_id': self.fund.id,
                'category_id': self.category.id,
                'description': 'Direct Move Create',
                'amount': 15.0,
                'payee_type': 'partner',
                'partner_id': self.partner.id,
                'move_id': dummy_move.id,
            })

    def test_19_sequence_no_gap_consecutive_numbering_after_failed_submit(self):
        """With no_gap implementation, failed submit does not burn sequence numbers."""
        v1 = self.env['petty.cash.voucher'].with_user(self.custodian_user).create({
            'fund_id': self.fund.id,
            'category_id': self.category.id,
            'description': 'Seq Test 1',
            'amount': 10.0,
            'payee_type': 'partner',
            'partner_id': self.partner.id,
        })
        v1.action_submit()
        self.assertEqual(v1.state, 'posted')
        n1 = int(v1.name.split('/')[-1])

        # v2 fails validation at submit (future date)
        v2 = self.env['petty.cash.voucher'].with_user(self.custodian_user).create({
            'fund_id': self.fund.id,
            'category_id': self.category.id,
            'description': 'Seq Test Failed',
            'amount': 10.0,
            'payee_type': 'partner',
            'partner_id': self.partner.id,
            'date': date.today() + timedelta(days=2),
        })
        with self.assertRaises(UserError):
            v2.action_submit()
        self.assertEqual(v2.name, 'New')

        # v3 succeeds and receives consecutive sequence number (n1 + 1)
        v3 = self.env['petty.cash.voucher'].with_user(self.custodian_user).create({
            'fund_id': self.fund.id,
            'category_id': self.category.id,
            'description': 'Seq Test 3',
            'amount': 10.0,
            'payee_type': 'partner',
            'partner_id': self.partner.id,
        })
        v3.action_submit()
        self.assertEqual(v3.state, 'posted')
        n3 = int(v3.name.split('/')[-1])
        self.assertEqual(n3, n1 + 1)

    def test_20_future_date_blocked_at_submit(self):
        """Vouchers with a future date cannot be submitted."""
        voucher_future = self.env['petty.cash.voucher'].with_user(self.custodian_user).create({
            'fund_id': self.fund.id,
            'category_id': self.category.id,
            'description': 'Future Expense',
            'date': date.today() + timedelta(days=3),
            'amount': 15.0,
            'payee_type': 'partner',
            'partner_id': self.partner.id,
        })
        with self.assertRaises(UserError) as cm:
            voucher_future.action_submit()
        self.assertIn("cannot be in the future", str(cm.exception))

    def test_21_reversal_blocked_when_fund_is_closed(self):
        """Reversing a posted voucher when the underlying fund is closed is blocked."""
        voucher = self.env['petty.cash.voucher'].with_user(self.custodian_user).create({
            'fund_id': self.fund.id,
            'category_id': self.category.id,
            'description': 'Voucher Before Fund Close',
            'amount': 12.0,
            'payee_type': 'partner',
            'partner_id': self.partner.id,
        })
        voucher.action_submit()
        self.assertEqual(voucher.state, 'posted')

        # Fund is closed by manager
        self.fund.write({'state': 'closed'})

        # Reversal attempt fails with clear error
        with self.assertRaises(UserError) as cm:
            wizard = self.env['petty.cash.voucher.reason.wizard'].with_user(self.manager_user).create({
                'voucher_id': voucher.id,
                'action_type': 'cancel',
                'reason': 'Late return',
            })
            wizard.action_confirm()
        self.assertIn("closed petty cash fund", str(cm.exception))

    def test_22_unique_voucher_number_per_company(self):
        """Two vouchers in the same company cannot share the same voucher number."""
        voucher1 = self.env['petty.cash.voucher'].create({
            'fund_id': self.fund.id,
            'category_id': self.category.id,
            'description': 'Voucher 1',
            'amount': 10.0,
            'payee_type': 'partner',
            'partner_id': self.partner.id,
        })
        voucher1._workflow_write({'name': 'PCV/UNIQUE/001'})

        voucher2 = self.env['petty.cash.voucher'].create({
            'fund_id': self.fund.id,
            'category_id': self.category.id,
            'description': 'Voucher 2',
            'amount': 10.0,
            'payee_type': 'partner',
            'partner_id': self.partner.id,
        })
        with self.assertRaises(Exception):
            with mute_logger('odoo.sql_db'):
                voucher2._workflow_write({'name': 'PCV/UNIQUE/001'})
                voucher2.flush_recordset(['name'])

    def test_23_ui_form_custodian_preselects_single_active_fund_and_submits(self):
        """Case 1a: Custodian with exactly 1 active fund gets it preselected via Form and submits successfully."""
        active_funds = self.env['petty.cash.fund'].search([
            ('state', '=', 'active'),
            ('custodian_ids', 'in', [self.custodian_user.id]),
        ])
        self.assertEqual(len(active_funds), 1)

        # Open Form view as custodian
        form = Form(self.env['petty.cash.voucher'].with_user(self.custodian_user))
        self.assertEqual(form.fund_id, self.fund)

        form.category_id = self.category
        form.payee_type = 'partner'
        form.partner_id = self.partner
        form.amount = 50.0
        form.description = 'Form UI Custodian Voucher'

        voucher = form.save()
        self.assertEqual(voucher.state, 'draft')
        self.assertEqual(voucher.fund_id, self.fund)

        voucher.action_submit()
        self.assertEqual(voucher.state, 'posted')
        self.assertTrue(bool(voucher.move_id))

    def test_24_ui_form_manager_flow_with_and_without_accounting(self):
        """Case 1b: Manager users with and without accounting rights can create and submit vouchers via Form."""
        manager_no_acct = new_test_user(
            self.env,
            login='voucher_mgr_no_acct_ui',
            groups='petty_cash_management.group_petty_cash_manager',
            company_id=self.company.id,
            company_ids=[Command.set(self.company.ids)],
        )
        form_no_acct = Form(self.env['petty.cash.voucher'].with_user(manager_no_acct))
        form_no_acct.fund_id = self.fund
        form_no_acct.category_id = self.category
        form_no_acct.payee_type = 'partner'
        form_no_acct.partner_id = self.partner
        form_no_acct.amount = 40.0
        form_no_acct.description = 'Form UI Manager No Acct'
        voucher_no_acct = form_no_acct.save()
        voucher_no_acct.action_submit()
        self.assertEqual(voucher_no_acct.state, 'posted')

        form_acct = Form(self.env['petty.cash.voucher'].with_user(self.manager_user))
        form_acct.fund_id = self.fund
        form_acct.category_id = self.category
        form_acct.payee_type = 'partner'
        form_acct.partner_id = self.partner
        form_acct.amount = 45.0
        form_acct.description = 'Form UI Manager With Acct'
        voucher_acct = form_acct.save()
        voucher_acct.action_submit()
        self.assertEqual(voucher_acct.state, 'posted')

    def test_25_ui_fund_selection_restrictions_state_custodian_multi_company(self):
        """Item 2: Freshly opened form state (company_id=False) fund_id domain evaluation & name_search.
        Proves it fails with the old check_company=True definition and passes with the fix.
        Tests:
        (a) Custodian with TWO active funds
        (b) Manager without accounting group
        (c) Manager with accounting group
        (d) Admin user
        Each returns expected active funds and excludes draft, closed, unassigned, and other-company funds.
        """
        # 1. Proof of failure with the old definition:
        old_domain_expr = "(company_id and [('company_id', 'in', [company_id] + [False])] or [('company_id', '=', False)]) + ([('state', '=', 'active')])"
        old_evaluated = safe_eval(old_domain_expr, {'company_id': False})
        self.assertEqual(old_evaluated, [('company_id', '=', False), ('state', '=', 'active')])
        # With the old definition, name_search returns empty list for all users because no fund has company_id=False
        old_res = self.env['petty.cash.fund'].with_user(self.custodian_user).name_search('', domain=old_evaluated)
        self.assertEqual(old_res, [], "With the old definition, dropdown is completely empty!")

        # 2. Freshly opened form state with the fix:
        fields_info = self.env['petty.cash.voucher'].fields_get(['fund_id'])
        domain_expr = fields_info['fund_id']['domain']
        evaluated_domain = safe_eval(domain_expr, {'company_id': False})
        self.assertEqual(evaluated_domain, [('state', '=', 'active')])

        # Helper to create journals
        def make_cash_journal(name, code, comp, acc, susp):
            return self.env['account.journal'].create({
                'name': name, 'code': code, 'type': 'cash', 'company_id': comp.id,
                'default_account_id': acc.id,
                'suspense_account_id': susp.id,
                'inbound_payment_method_line_ids': [
                    Command.create({
                        'payment_method_id': self.env.ref('account.account_payment_method_manual_in').id,
                        'payment_account_id': acc.id,
                    }),
                ],
                'outbound_payment_method_line_ids': [
                    Command.create({
                        'payment_method_id': self.env.ref('account.account_payment_method_manual_out').id,
                        'payment_account_id': acc.id,
                    }),
                ],
            })

        # 3. Setup funds in self.company:
        # Fund 1: self.fund (active, custodian_user in custodian_ids)
        # Fund 2: second active fund for self.custodian_user
        j2 = make_cash_journal('Cash 2', 'PC25_2', self.company, self.petty_cash_account, self.suspense_account)
        fund_2 = self.env['petty.cash.fund'].create({
            'name': 'Active Fund 2',
            'company_id': self.company.id,
            'journal_id': j2.id,
            'custodian_ids': [Command.link(self.custodian_user.id)],
        })
        fund_2.action_activate()

        # Fund Draft: draft state, custodian_user is custodian
        jd = make_cash_journal('Cash Draft', 'PC25_D', self.company, self.petty_cash_account, self.suspense_account)
        fund_draft = self.env['petty.cash.fund'].create({
            'name': 'Draft Fund',
            'company_id': self.company.id,
            'journal_id': jd.id,
            'custodian_ids': [Command.link(self.custodian_user.id)],
        })

        # Fund Closed: closed state, custodian_user is custodian
        jc = make_cash_journal('Cash Closed', 'PC25_C', self.company, self.petty_cash_account, self.suspense_account)
        fund_closed = self.env['petty.cash.fund'].create({
            'name': 'Closed Fund',
            'company_id': self.company.id,
            'journal_id': jc.id,
            'custodian_ids': [Command.link(self.custodian_user.id)],
        })
        fund_closed.action_activate()
        fund_closed.action_close()

        # Fund Unassigned: active, custodian is ONLY custodian_user_2 (not self.custodian_user)
        ju = make_cash_journal('Cash Unassigned', 'PC25_U', self.company, self.petty_cash_account, self.suspense_account)
        fund_unassigned = self.env['petty.cash.fund'].create({
            'name': 'Unassigned Fund',
            'company_id': self.company.id,
            'journal_id': ju.id,
            'custodian_ids': [Command.link(self.custodian_user_2.id)],
        })
        fund_unassigned.action_activate()

        # Fund Company 2: active, in company 2
        comp2 = self.env['res.company'].create({
            'name': 'Company 2 Test25',
            'currency_id': self.company.currency_id.id,
        })
        custodian_comp2 = new_test_user(
            self.env,
            login='custodian_comp2_test25',
            groups='petty_cash_management.group_petty_cash_custodian',
            company_id=comp2.id,
            company_ids=[Command.set([comp2.id])],
        )
        comp2_acc = self.env['account.account'].create({
            'name': 'Comp2 Cash',
            'code': '101995',
            'account_type': 'asset_cash',
            'company_ids': [Command.link(comp2.id)],
        })
        j_comp2 = make_cash_journal('Cash Comp2', 'PC25_C2', comp2, comp2_acc, comp2_acc)
        fund_comp2 = self.env['petty.cash.fund'].create({
            'name': 'Company 2 Fund',
            'company_id': comp2.id,
            'journal_id': j_comp2.id,
            'custodian_ids': [Command.link(custodian_comp2.id)],
        })
        fund_comp2.action_activate()

        # Create Manager without accounting group
        manager_no_acct = new_test_user(
            self.env,
            login='mgr_no_acct_test25',
            groups='petty_cash_management.group_petty_cash_manager',
            company_id=self.company.id,
            company_ids=[Command.set(self.company.ids)],
        )

        # (a) Custodian with TWO active funds (self.fund and fund_2):
        # Must return self.fund and fund_2
        # Must exclude draft, closed, unassigned, and other-company funds
        res_a = self.env['petty.cash.fund'].with_user(self.custodian_user).name_search('', domain=evaluated_domain)
        fund_ids_a = {r[0] for r in res_a}
        self.assertEqual(fund_ids_a, {self.fund.id, fund_2.id})
        self.assertNotIn(fund_draft.id, fund_ids_a)
        self.assertNotIn(fund_closed.id, fund_ids_a)
        self.assertNotIn(fund_unassigned.id, fund_ids_a)
        self.assertNotIn(fund_comp2.id, fund_ids_a)

        # (b) Manager without accounting group:
        # Must see ALL active funds in self.company (self.fund, fund_2, fund_unassigned)
        # Must exclude draft, closed, and other-company funds
        res_b = self.env['petty.cash.fund'].with_user(manager_no_acct).name_search('', domain=evaluated_domain)
        fund_ids_b = {r[0] for r in res_b}
        self.assertEqual(fund_ids_b, {self.fund.id, fund_2.id, fund_unassigned.id})
        self.assertNotIn(fund_draft.id, fund_ids_b)
        self.assertNotIn(fund_closed.id, fund_ids_b)
        self.assertNotIn(fund_comp2.id, fund_ids_b)

        # (c) Manager with accounting:
        # Must see ALL active funds in self.company
        # Must exclude draft, closed, and other-company funds
        res_c = self.env['petty.cash.fund'].with_user(self.manager_user).name_search('', domain=evaluated_domain)
        fund_ids_c = {r[0] for r in res_c}
        self.assertEqual(fund_ids_c, {self.fund.id, fund_2.id, fund_unassigned.id})
        self.assertNotIn(fund_draft.id, fund_ids_c)
        self.assertNotIn(fund_closed.id, fund_ids_c)
        self.assertNotIn(fund_comp2.id, fund_ids_c)

        # (d) Admin user:
        # In self.company: must see all active funds in self.company
        # Must exclude draft, closed, and other-company funds
        admin_user = self.env.ref('base.user_admin')
        admin_user.write({'company_ids': [Command.link(self.company.id)]})
        res_d = self.env['petty.cash.fund'].with_user(admin_user).with_company(self.company).name_search('', domain=evaluated_domain)
        fund_ids_d = {r[0] for r in res_d}
        self.assertEqual(fund_ids_d, {self.fund.id, fund_2.id, fund_unassigned.id})
        self.assertNotIn(fund_draft.id, fund_ids_d)
        self.assertNotIn(fund_closed.id, fund_ids_d)
        self.assertNotIn(fund_comp2.id, fund_ids_d)

    def test_26_ui_form_submit_insufficient_balance_shows_both_numbers(self):
        """Case 5: Submitting a voucher when balance is lower than amount displays both formatted numbers."""
        self.fund.write({'per_voucher_limit': 100000.0, 'approval_threshold': 100000.0})
        self.fund.invalidate_recordset(['balance'])
        current_bal = self.fund.balance

        form = Form(self.env['petty.cash.voucher'].with_user(self.custodian_user))
        form.category_id = self.category
        form.payee_type = 'partner'
        form.partner_id = self.partner
        form.amount = current_bal + 500.0
        form.description = 'Form UI Overdraft'

        voucher = form.save()
        with self.assertRaises(UserError) as cm:
            voucher.action_submit()

        err_msg = str(cm.exception)
        self.assertIn("Insufficient funds", err_msg)
        expected_bal = formatLang(self.env, current_bal, currency_obj=self.fund.currency_id)
        expected_amt = formatLang(self.env, current_bal + 500.0, currency_obj=self.fund.currency_id)
        self.assertIn(expected_bal, err_msg)
        self.assertIn(expected_amt, err_msg)
