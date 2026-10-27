from datetime import timedelta

from odoo import Command, fields
from odoo.exceptions import AccessError, UserError, ValidationError
from odoo.tests import Form, tagged, new_test_user
from odoo.tools.safe_eval import safe_eval
from odoo.addons.account.tests.common import AccountTestInvoicingCommon


@tagged('post_install', '-at_install')
class TestPettyCashTopup(AccountTestInvoicingCommon):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()

        cls.env.user.write({
            'group_ids': [
                Command.link(cls.env.ref('petty_cash_management.group_petty_cash_manager').id),
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
            'name': 'Petty Cash Journal Topup',
            'code': 'PCTJ',
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

        # Bank account for funding source
        cls.bank_source_account = cls.env['account.account'].create({
            'name': 'Main Bank Account',
            'code': '102000',
            'account_type': 'asset_cash',
            'company_ids': [Command.link(cls.company.id)],
        })

        # Expense account & Category for end-to-end voucher test
        cls.expense_account = cls.env['account.account'].create({
            'name': 'Office Expenses',
            'code': '600095',
            'account_type': 'expense',
            'company_ids': [Command.link(cls.company.id)],
        })
        cls.category = cls.env['petty.cash.category'].create({
            'name': 'Office Supplies',
            'expense_account_id': cls.expense_account.id,
            'company_id': cls.company.id,
            'receipt_required': False,
        })

        cls.partner = cls.env['res.partner'].create({
            'name': 'Office Mart Supplies',
            'company_id': cls.company.id,
        })

        # Users
        cls.custodian_user = new_test_user(
            cls.env,
            login='custodian_topup_test',
            groups='petty_cash_management.group_petty_cash_custodian',
            company_id=cls.company.id,
            company_ids=[Command.set(cls.company.ids)],
        )
        cls.custodian_user_other = new_test_user(
            cls.env,
            login='custodian_topup_other',
            groups='petty_cash_management.group_petty_cash_custodian',
            company_id=cls.company.id,
            company_ids=[Command.set(cls.company.ids)],
        )
        cls.manager_billing = new_test_user(
            cls.env,
            login='manager_billing_topup',
            groups='petty_cash_management.group_petty_cash_manager,account.group_account_invoice',
            company_id=cls.company.id,
            company_ids=[Command.set(cls.company.ids)],
        )
        cls.manager_no_billing = new_test_user(
            cls.env,
            login='manager_no_billing_topup',
            groups='petty_cash_management.group_petty_cash_manager',
            company_id=cls.company.id,
            company_ids=[Command.set(cls.company.ids)],
        )

        # Petty Cash Fund
        cls.fund = cls.env['petty.cash.fund'].create({
            'name': 'HQ Operational Fund',
            'journal_id': cls.journal.id,
            'custodian_ids': [Command.set([cls.custodian_user.id])],
            'company_id': cls.company.id,
        })
        cls.fund.action_activate()

        # Top-up Source
        cls.source = cls.env['petty.cash.topup.source'].create({
            'name': 'Primary Bank Account',
            'account_id': cls.bank_source_account.id,
            'company_id': cls.company.id,
        })

    def test_01_manager_with_billing_posts_topup_and_voucher_end_to_end(self):
        """Manager with Billing posts top-up: exact 2 move lines (accounts, dr/cr, ref, date),
        fund balance rises, then a voucher posts using that cash.
        """
        initial_bal = self.fund.balance
        self.assertEqual(initial_bal, 0.0)

        topup = self.env['petty.cash.topup'].with_user(self.manager_billing).create({
            'fund_id': self.fund.id,
            'source_id': self.source.id,
            'amount': 500.0,
            'date': fields.Date.today(),
            'notes': 'HQ fund initial top-up',
        })
        self.assertEqual(topup.state, 'draft')
        self.assertEqual(topup.name, 'New')

        topup.action_post()

        self.assertEqual(topup.state, 'posted')
        self.assertNotEqual(topup.name, 'New')
        self.assertTrue(topup.name.startswith('PCT/'))
        self.assertEqual(topup.posted_by_id, self.manager_billing)
        self.assertTrue(topup.posted_date)

        # Check linked journal entry
        move = topup.move_id
        self.assertTrue(move)
        self.assertEqual(move.state, 'posted')
        self.assertEqual(move.journal_id, self.fund.journal_id)
        self.assertEqual(move.ref, topup.name)
        self.assertEqual(move.date, topup.date)
        self.assertEqual(len(move.line_ids), 2)

        # Debit line: Fund Default Cash Account
        line_dr = move.line_ids.filtered(lambda l: l.debit > 0)
        self.assertEqual(len(line_dr), 1)
        self.assertEqual(line_dr.account_id, self.fund.journal_id.default_account_id)
        self.assertEqual(line_dr.debit, 500.0)
        self.assertEqual(line_dr.credit, 0.0)
        self.assertEqual(line_dr.name, f"Top-up from {self.source.name}")
        self.assertFalse(line_dr.partner_id)

        # Credit line: Source Account
        line_cr = move.line_ids.filtered(lambda l: l.credit > 0)
        self.assertEqual(len(line_cr), 1)
        self.assertEqual(line_cr.account_id, self.source.account_id)
        self.assertEqual(line_cr.debit, 0.0)
        self.assertEqual(line_cr.credit, 500.0)
        self.assertEqual(line_cr.name, f"Top-up from {self.source.name}")
        self.assertFalse(line_cr.partner_id)

        # Fund balance is updated
        self.fund.invalidate_recordset(['balance'])
        self.assertEqual(self.fund.balance, 500.0)

        # Smart button to journal entry
        action = topup.with_user(self.manager_billing).action_view_journal_entry()
        self.assertEqual(action['res_id'], move.id)

        # End-to-end: Custodian spends cash via voucher
        voucher = self.env['petty.cash.voucher'].with_user(self.custodian_user).create({
            'fund_id': self.fund.id,
            'category_id': self.category.id,
            'amount': 150.0,
            'description': 'Office stationary',
            'payee_type': 'partner',
            'partner_id': self.partner.id,
        })
        voucher.action_submit()
        self.assertEqual(voucher.state, 'posted')

        self.fund.invalidate_recordset(['balance'])
        self.assertEqual(self.fund.balance, 350.0)

    def test_02_manager_without_accounting_group_flow(self):
        """A Manager WITHOUT any accounting group can create and cancel a draft top-up,
        and gets the clear Billing-group error on post.
        """
        # 1. Manager without accounting group creates draft top-up
        topup = self.env['petty.cash.topup'].with_user(self.manager_no_billing).create({
            'fund_id': self.fund.id,
            'source_id': self.source.id,
            'amount': 200.0,
            'notes': 'Created by Manager without any accounting group',
        })
        self.assertEqual(topup.state, 'draft')
        self.assertEqual(topup.name, 'New')

        # 2. On post, gets clear Billing-group error and nothing is created
        with self.assertRaises(UserError) as cm:
            topup.action_post()

        err_msg = str(cm.exception)
        self.assertIn("account.group_account_invoice", err_msg)
        self.assertEqual(topup.state, 'draft')
        self.assertEqual(topup.name, 'New')
        self.assertFalse(topup.move_id)

        # Fund balance remains unchanged
        self.fund.invalidate_recordset(['balance'])
        self.assertEqual(self.fund.balance, 0.0)

        # 3. Manager without accounting group can cancel the draft top-up
        topup.action_cancel()
        self.assertEqual(topup.state, 'cancelled')

        # 4. Another draft top-up can be created and cancelled directly
        topup_2 = self.env['petty.cash.topup'].with_user(self.manager_no_billing).create({
            'fund_id': self.fund.id,
            'source_id': self.source.id,
            'amount': 120.0,
        })
        self.assertEqual(topup_2.state, 'draft')
        topup_2.action_cancel()
        self.assertEqual(topup_2.state, 'cancelled')

    def test_03_custodian_permissions_restricted(self):
        """Custodian cannot create, write, or post top-ups, cannot access sources,
        and can read only top-ups of own funds.
        """
        # Custodian cannot access top-up sources
        with self.assertRaises(AccessError):
            self.env['petty.cash.topup.source'].with_user(self.custodian_user).search([])

        # Custodian cannot create a top-up
        with self.assertRaises(AccessError):
            self.env['petty.cash.topup'].with_user(self.custodian_user).create({
                'fund_id': self.fund.id,
                'source_id': self.source.id,
                'amount': 100.0,
            })

        # Manager creates a draft top-up
        topup = self.env['petty.cash.topup'].with_user(self.manager_billing).create({
            'fund_id': self.fund.id,
            'source_id': self.source.id,
            'amount': 100.0,
        })

        # Custodian cannot write or post
        with self.assertRaises(AccessError):
            topup.with_user(self.custodian_user).write({'amount': 200.0})
        with self.assertRaises(UserError):
            topup.with_user(self.custodian_user).action_post()

        # Custodian can read top-up of own assigned fund
        topups_seen = self.env['petty.cash.topup'].with_user(self.custodian_user).search([('id', '=', topup.id)])
        self.assertEqual(len(topups_seen), 1)

        # Custodian of another fund cannot read this top-up
        topups_unseen = self.env['petty.cash.topup'].with_user(self.custodian_user_other).search([('id', '=', topup.id)])
        self.assertEqual(len(topups_unseen), 0)

    def test_04_source_and_model_validations(self):
        """Source validations: expense/income rejected; source equal to fund default account rejected;
        source of another company rejected.
        """
        # 1. Expense account rejected
        with self.assertRaises(ValidationError):
            self.env['petty.cash.topup.source'].create({
                'name': 'Expense Source',
                'account_id': self.expense_account.id,
                'company_id': self.company.id,
            })

        # 2. Income account rejected
        income_account = self.env['account.account'].create({
            'name': 'Operating Income',
            'code': '400095',
            'account_type': 'income',
            'company_ids': [Command.link(self.company.id)],
        })
        with self.assertRaises(ValidationError):
            self.env['petty.cash.topup.source'].create({
                'name': 'Income Source',
                'account_id': income_account.id,
                'company_id': self.company.id,
            })

        # 3. Source equal to fund default cash account rejected
        same_acc_source = self.env['petty.cash.topup.source'].create({
            'name': 'Same Cash Account Source',
            'account_id': self.fund.journal_id.default_account_id.id,
            'company_id': self.company.id,
        })
        with self.assertRaises(ValidationError):
            self.env['petty.cash.topup'].create({
                'fund_id': self.fund.id,
                'source_id': same_acc_source.id,
                'amount': 100.0,
            })

        # 4. Source of another company rejected
        comp2 = self.env['res.company'].create({'name': 'Company 2 Test'})
        comp2_acc = self.env['account.account'].create({
            'name': 'Comp 2 Bank',
            'code': '102099',
            'account_type': 'asset_cash',
            'company_ids': [Command.link(comp2.id)],
        })
        comp2_source = self.env['petty.cash.topup.source'].create({
            'name': 'Comp 2 Source',
            'account_id': comp2_acc.id,
            'company_id': comp2.id,
        })
        with self.assertRaises(ValidationError):
            self.env['petty.cash.topup'].create({
                'fund_id': self.fund.id,
                'source_id': comp2_source.id,
                'amount': 100.0,
            })

        # 5. Non-positive amount rejected
        with self.assertRaises(ValidationError):
            self.env['petty.cash.topup'].create({
                'fund_id': self.fund.id,
                'source_id': self.source.id,
                'amount': 0.0,
            })
        with self.assertRaises(ValidationError):
            self.env['petty.cash.topup'].create({
                'fund_id': self.fund.id,
                'source_id': self.source.id,
                'amount': -50.0,
            })

    def test_05_date_validations_and_lock_dates(self):
        """Future date rejected; posting into locked period rejected."""
        # Future date rejected at post
        topup_future = self.env['petty.cash.topup'].with_user(self.manager_billing).create({
            'fund_id': self.fund.id,
            'source_id': self.source.id,
            'amount': 100.0,
            'date': fields.Date.today() + timedelta(days=5),
        })
        with self.assertRaises(UserError) as cm:
            topup_future.action_post()
        self.assertIn("future", str(cm.exception).lower())

        # Posting into locked period rejected
        lock_date = fields.Date.today() - timedelta(days=5)
        self.company.write({'fiscalyear_lock_date': lock_date})
        topup_locked = self.env['petty.cash.topup'].with_user(self.manager_billing).create({
            'fund_id': self.fund.id,
            'source_id': self.source.id,
            'amount': 100.0,
            'date': lock_date - timedelta(days=2),
        })
        with self.assertRaises(UserError):
            topup_locked.action_post()
        self.company.write({'fiscalyear_lock_date': False})

    def test_06_server_side_workflow_protection_and_cancellation(self):
        """Workflow fields write blocked; posted top-up immutable; only draft deletable;
        cancel draft works; cancel posted blocked.
        """
        topup = self.env['petty.cash.topup'].with_user(self.manager_billing).create({
            'fund_id': self.fund.id,
            'source_id': self.source.id,
            'amount': 100.0,
        })

        # Server-side workflow protection against direct writes
        with self.assertRaises(UserError):
            topup.write({'state': 'posted'})
        with self.assertRaises(UserError):
            topup.write({'name': 'PCT/2026/00099'})
        with self.assertRaises(UserError):
            topup.write({'move_id': 1})
        with self.assertRaises(UserError):
            topup.write({'posted_by_id': self.env.user.id})
        with self.assertRaises(UserError):
            topup.write({'posted_date': fields.Datetime.now()})

        # Cancel draft works
        topup.action_cancel()
        self.assertEqual(topup.state, 'cancelled')

        # Create and post another topup
        topup_posted = self.env['petty.cash.topup'].with_user(self.manager_billing).create({
            'fund_id': self.fund.id,
            'source_id': self.source.id,
            'amount': 250.0,
        })
        topup_posted.action_post()
        self.assertEqual(topup_posted.state, 'posted')

        # Cancel posted blocked
        with self.assertRaises(UserError) as cm:
            topup_posted.action_cancel()
        self.assertIn("accountant must reverse", str(cm.exception).lower())

        # Posted top-up is immutable
        with self.assertRaises(UserError):
            topup_posted.write({'amount': 300.0})
        with self.assertRaises(UserError):
            topup_posted.write({'notes': 'Hacked notes'})

        # Only draft deletable
        with self.assertRaises(UserError):
            topup_posted.unlink()
        with self.assertRaises(UserError):
            topup.unlink()  # Cancelled cannot be deleted either

        topup_draft_delete = self.env['petty.cash.topup'].with_user(self.manager_billing).create({
            'fund_id': self.fund.id,
            'source_id': self.source.id,
            'amount': 75.0,
        })
        topup_draft_delete.unlink()

    def test_07_sequence_consecutive_after_failed_post(self):
        """Sequence is drawn only after all validations pass, ensuring no sequence gaps after failed posts."""
        # Top-up that fails validation (inactive fund)
        journal_inactive = self.env['account.journal'].create({
            'name': 'Cash Inactive Jnl',
            'code': 'PCINA',
            'type': 'cash',
            'company_id': self.company.id,
            'default_account_id': self.petty_cash_account.id,
            'suspense_account_id': self.suspense_account.id,
        })
        fund_inactive = self.env['petty.cash.fund'].create({
            'name': 'Draft Fund',
            'journal_id': journal_inactive.id,
            'company_id': self.company.id,
        })  # Still in draft state

        topup_fail = self.env['petty.cash.topup'].with_user(self.manager_billing).create({
            'fund_id': fund_inactive.id,
            'source_id': self.source.id,
            'amount': 100.0,
        })
        with self.assertRaises(UserError):
            topup_fail.action_post()
        self.assertEqual(topup_fail.name, 'New')

        # Successful top-up 1
        topup_1 = self.env['petty.cash.topup'].with_user(self.manager_billing).create({
            'fund_id': self.fund.id,
            'source_id': self.source.id,
            'amount': 100.0,
        })
        topup_1.action_post()

        # Successful top-up 2
        topup_2 = self.env['petty.cash.topup'].with_user(self.manager_billing).create({
            'fund_id': self.fund.id,
            'source_id': self.source.id,
            'amount': 200.0,
        })
        topup_2.action_post()

        # Sequence numbers must be consecutive
        num_1 = int(topup_1.name.split('/')[-1])
        num_2 = int(topup_2.name.split('/')[-1])
        self.assertEqual(num_2, num_1 + 1)

    def test_08_multi_company_and_fresh_form_dropdown(self):
        """Multi-company isolation and fund dropdown on a fresh form via fields_get domain
        with company_id=False for admin, Manager, and custodian.
        """
        # Create second active fund for custodian
        journal_2 = self.env['account.journal'].create({
            'name': 'Cash 2 Topup',
            'code': 'PC2T',
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
            'name': 'Second Active Fund',
            'company_id': self.company.id,
            'journal_id': journal_2.id,
            'custodian_ids': [Command.set([self.custodian_user.id])],
        })
        fund_2.action_activate()

        # Inactive fund in company 1
        journal_draft = self.env['account.journal'].create({
            'name': 'Cash Draft Topup',
            'code': 'PCDT',
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
        fund_draft = self.env['petty.cash.fund'].create({
            'name': 'Draft Fund Unselectable',
            'company_id': self.company.id,
            'journal_id': journal_draft.id,
            'custodian_ids': [Command.set([self.custodian_user.id])],
        })

        # Company 2 fund
        comp2 = self.env['res.company'].create({'name': 'Company 2 Topup Isolated'})
        comp2_acc = self.env['account.account'].create({
            'name': 'Comp 2 Cash Acc',
            'code': '101088',
            'account_type': 'asset_cash',
            'company_ids': [Command.link(comp2.id)],
        })
        journal_comp2 = self.env['account.journal'].create({
            'name': 'Comp 2 Cash Jnl',
            'code': 'PC2J',
            'type': 'cash',
            'company_id': comp2.id,
            'default_account_id': comp2_acc.id,
            'suspense_account_id': comp2_acc.id,
            'inbound_payment_method_line_ids': [
                Command.create({
                    'payment_method_id': self.env.ref('account.account_payment_method_manual_in').id,
                    'payment_account_id': comp2_acc.id,
                }),
            ],
            'outbound_payment_method_line_ids': [
                Command.create({
                    'payment_method_id': self.env.ref('account.account_payment_method_manual_out').id,
                    'payment_account_id': comp2_acc.id,
                }),
            ],
        })
        custodian_comp2 = new_test_user(
            self.env,
            login='custodian_topup_comp2',
            groups='petty_cash_management.group_petty_cash_custodian',
            company_id=comp2.id,
            company_ids=[Command.set(comp2.ids)],
        )
        fund_comp2 = self.env['petty.cash.fund'].create({
            'name': 'Comp 2 Active Fund',
            'company_id': comp2.id,
            'journal_id': journal_comp2.id,
            'custodian_ids': [Command.link(custodian_comp2.id)],
        })
        fund_comp2.action_activate()

        # Evaluate domain from fields_get on petty.cash.topup with company_id=False
        field_domain_str = self.env['petty.cash.topup'].fields_get(['fund_id'])['fund_id']['domain']
        evaluated_domain = safe_eval(field_domain_str, {'company_id': False})

        # (a) Custodian with two active funds:
        res_a = self.env['petty.cash.fund'].with_user(self.custodian_user).name_search('', domain=evaluated_domain)
        fund_ids_a = {r[0] for r in res_a}
        self.assertEqual(fund_ids_a, {self.fund.id, fund_2.id})
        self.assertNotIn(fund_draft.id, fund_ids_a)
        self.assertNotIn(fund_comp2.id, fund_ids_a)

        # (b) Manager:
        res_b = self.env['petty.cash.fund'].with_user(self.manager_billing).name_search('', domain=evaluated_domain)
        fund_ids_b = {r[0] for r in res_b}
        self.assertEqual(fund_ids_b, {self.fund.id, fund_2.id})
        self.assertNotIn(fund_draft.id, fund_ids_b)
        self.assertNotIn(fund_comp2.id, fund_ids_b)

        # (c) Admin:
        admin_user = self.env.ref('base.user_admin')
        admin_user.write({'company_ids': [Command.link(self.company.id)]})
        res_c = self.env['petty.cash.fund'].with_user(admin_user).with_company(self.company).name_search('', domain=evaluated_domain)
        fund_ids_c = {r[0] for r in res_c}
        self.assertEqual(fund_ids_c, {self.fund.id, fund_2.id})
        self.assertNotIn(fund_draft.id, fund_ids_c)
        self.assertNotIn(fund_comp2.id, fund_ids_c)

    def test_09_ui_form_flow_from_fund_header_button(self):
        """Manager opens top-up from fund header button context (fund preselected), saves and posts.
        Fund topup_count stat button increments.
        """
        # Open action from fund
        act = self.fund.with_user(self.manager_billing).action_open_new_topup()
        self.assertEqual(act['res_model'], 'petty.cash.topup')
        self.assertEqual(act['context'].get('default_fund_id'), self.fund.id)

        # UI Form flow
        form = Form(self.env['petty.cash.topup'].with_user(self.manager_billing).with_context(act['context']))
        self.assertEqual(form.fund_id, self.fund)
        form.source_id = self.source
        form.amount = 450.0
        form.notes = 'Top-up initiated from fund header button'

        topup = form.save()
        self.assertEqual(topup.state, 'draft')
        self.assertEqual(topup.fund_id, self.fund)
        self.assertEqual(topup.amount, 450.0)

        # Post top-up
        topup.action_post()
        self.assertEqual(topup.state, 'posted')

        # Fund topup_count reflects the new top-up
        self.fund.invalidate_recordset(['topup_count', 'balance'])
        self.assertEqual(self.fund.topup_count, 1)
        self.assertEqual(self.fund.balance, 450.0)

        # Stat button action returns the top-up
        stat_act = self.fund.action_view_topups()
        self.assertEqual(stat_act['domain'], [('fund_id', '=', self.fund.id)])
