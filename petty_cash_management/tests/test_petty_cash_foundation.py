import psycopg2
from odoo import Command
from odoo.exceptions import AccessError, UserError, ValidationError
from odoo.tests import tagged, new_test_user
from odoo.tools import mute_logger
from odoo.addons.account.tests.common import AccountTestInvoicingCommon


@tagged('post_install', '-at_install')
class TestPettyCashFoundation(AccountTestInvoicingCommon):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()

        # Company currency
        cls.company = cls.env.company
        cls.currency = cls.company.currency_id

        # Distinct accounts for petty cash cash journal
        cls.petty_cash_account = cls.env['account.account'].create({
            'name': 'Petty Cash Liquidity Account',
            'code': '101099',
            'account_type': 'asset_cash',
            'company_ids': [Command.link(cls.company.id)],
        })
        cls.suspense_account = cls.env['account.account'].create({
            'name': 'Petty Cash Suspense Account',
            'code': '101098',
            'account_type': 'asset_current',
            'company_ids': [Command.link(cls.company.id)],
        })
        cls.outstanding_receipt_account = cls.env['account.account'].create({
            'name': 'Petty Cash Outstanding Receipts',
            'code': '101097',
            'account_type': 'asset_current',
            'company_ids': [Command.link(cls.company.id)],
        })
        cls.outstanding_payment_account = cls.env['account.account'].create({
            'name': 'Petty Cash Outstanding Payments',
            'code': '101096',
            'account_type': 'asset_current',
            'company_ids': [Command.link(cls.company.id)],
        })

        # Category expense account
        cls.expense_account = cls.env['account.account'].create({
            'name': 'Petty Cash Office Expenses',
            'code': '601099',
            'account_type': 'expense',
            'company_ids': [Command.link(cls.company.id)],
        })

        # Primary cash journal
        cls.petty_cash_journal = cls.env['account.journal'].create({
            'name': 'Head Office Cash Journal',
            'type': 'cash',
            'code': 'PC1',
            'company_id': cls.company.id,
            'default_account_id': cls.petty_cash_account.id,
            'suspense_account_id': cls.suspense_account.id,
        })
        cls.petty_cash_journal.inbound_payment_method_line_ids.write({
            'payment_account_id': cls.outstanding_receipt_account.id,
        })
        cls.petty_cash_journal.outbound_payment_method_line_ids.write({
            'payment_account_id': cls.outstanding_payment_account.id,
        })

        # Secondary cash journal for multi-fund tests
        cls.petty_cash_account_2 = cls.env['account.account'].create({
            'name': 'Petty Cash Liquidity Account 2',
            'code': '101089',
            'account_type': 'asset_cash',
            'company_ids': [Command.link(cls.company.id)],
        })
        cls.petty_cash_journal_2 = cls.env['account.journal'].create({
            'name': 'Branch Cash Journal',
            'type': 'cash',
            'code': 'PC2',
            'company_id': cls.company.id,
            'default_account_id': cls.petty_cash_account_2.id,
            'suspense_account_id': cls.suspense_account.id,
        })
        cls.petty_cash_journal_2.inbound_payment_method_line_ids.write({
            'payment_account_id': cls.outstanding_receipt_account.id,
        })
        cls.petty_cash_journal_2.outbound_payment_method_line_ids.write({
            'payment_account_id': cls.outstanding_payment_account.id,
        })

        # Test users
        cls.custodian_user = new_test_user(
            cls.env,
            login='custodian_1',
            name='Custodian One',
            groups='petty_cash_management.group_petty_cash_custodian',
            company_id=cls.company.id,
            company_ids=[Command.set(cls.company.ids)],
        )
        cls.custodian_user_2 = new_test_user(
            cls.env,
            login='custodian_2',
            name='Custodian Two',
            groups='petty_cash_management.group_petty_cash_custodian',
            company_id=cls.company.id,
            company_ids=[Command.set(cls.company.ids)],
        )
        cls.manager_user = new_test_user(
            cls.env,
            login='petty_manager',
            name='Petty Manager',
            groups='petty_cash_management.group_petty_cash_manager',
            company_id=cls.company.id,
            company_ids=[Command.set(cls.company.ids)],
        )
        cls.approver_user = new_test_user(
            cls.env,
            login='petty_approver',
            name='Petty Approver',
            groups='petty_cash_management.group_petty_cash_approver',
            company_id=cls.company.id,
            company_ids=[Command.set(cls.company.ids)],
        )

        # Grant Petty Cash Manager group to test runner user
        cls.env.user.write({
            'group_ids': [
                Command.link(cls.env.ref('petty_cash_management.group_petty_cash_manager').id),
            ],
        })

    def test_activation_validation(self):
        """Test all missing configuration failure cases during action_activate, then succeed."""
        fund = self.env['petty.cash.fund'].create({
            'name': 'Test Validation Fund',
            'company_id': self.company.id,
        })

        # 1. No journal and no custodian
        with self.assertRaises(UserError) as cm:
            fund.action_activate()
        self.assertIn("Petty Cash Journal must be set", str(cm.exception))
        self.assertIn("At least one custodian must be assigned", str(cm.exception))

        # 1b. Valid journal set but missing custodian alone
        fund.journal_id = self.petty_cash_journal
        with self.assertRaises(UserError) as cm:
            fund.action_activate()
        self.assertIn("At least one custodian must be assigned", str(cm.exception))
        fund.journal_id = False

        # Assign custodian
        fund.custodian_ids = [Command.link(self.custodian_user.id)]

        # 2. Journal not of type cash (use bank journal)
        bank_journal = self.company_data['default_journal_bank']
        fund.journal_id = bank_journal
        with self.assertRaises(UserError) as cm:
            fund.action_activate()
        self.assertIn("must be of type 'Cash'", str(cm.exception))

        # 3. Journal company mismatch is rejected by check_company AND action_activate
        other_company = self.env['res.company'].create({'name': 'Foreign Company'})
        journal_other_comp = self.env['account.journal'].create({
            'name': 'Foreign Cash Journal',
            'type': 'cash',
            'code': 'FPC',
            'company_id': other_company.id,
        })
        with self.assertRaises(UserError) as cm:
            fund.journal_id = journal_other_comp
        self.assertIn("company inconsistencies", str(cm.exception))

        # Test action_activate's own company mismatch check directly
        self.env.cr.execute(
            'UPDATE petty_cash_fund SET journal_id = %s WHERE id = %s',
            (journal_other_comp.id, fund.id),
        )
        fund.invalidate_recordset(['journal_id'])
        with self.assertRaises(UserError) as cm:
            fund.action_activate()
        self.assertIn("must match fund company", str(cm.exception))
        fund.journal_id = False

        # 4. Incomplete cash journal (missing default account, suspense account, outstanding accounts)
        incomplete_journal = self.env['account.journal'].create({
            'name': 'Incomplete Cash Journal',
            'type': 'cash',
            'code': 'INC1',
            'company_id': self.company.id,
        })
        incomplete_journal.default_account_id = False
        incomplete_journal.suspense_account_id = False
        fund.journal_id = incomplete_journal
        with self.assertRaises(UserError) as cm:
            fund.action_activate()
        self.assertIn("default account (Petty Cash account) is not set", str(cm.exception))
        self.assertIn("suspense account is not set", str(cm.exception))
        self.assertIn("outstanding receipts account", str(cm.exception))
        self.assertIn("outstanding payments account", str(cm.exception))

        # 5. Default account wrong account_type (expense instead of asset_cash)
        incomplete_journal.default_account_id = self.expense_account
        incomplete_journal.suspense_account_id = self.suspense_account
        with self.assertRaises(UserError) as cm:
            fund.action_activate()
        self.assertIn("must be of type 'Bank and Cash'", str(cm.exception))

        # 6. Now set fully configured valid journal -> activate succeeds
        fund.journal_id = self.petty_cash_journal
        fund.action_activate()
        self.assertEqual(fund.state, 'active')

    def test_one_fund_per_journal(self):
        """Enforce unique constraint of one fund per journal."""
        self.env['petty.cash.fund'].create({
            'name': 'Fund 1',
            'journal_id': self.petty_cash_journal.id,
            'custodian_ids': [Command.link(self.custodian_user.id)],
        })
        with mute_logger('odoo.sql_db'), self.assertRaises(psycopg2.IntegrityError) as cm:
            with self.env.cr.savepoint():
                self.env['petty.cash.fund'].create({
                    'name': 'Fund 2 duplicate journal',
                    'journal_id': self.petty_cash_journal.id,
                    'custodian_ids': [Command.link(self.custodian_user.id)],
                })
        self.assertIn("petty_cash_fund_journal_uniq", str(cm.exception))
        msg = self.env['petty.cash.fund']._sql_error_to_message(cm.exception)
        self.assertEqual(msg, "A petty cash fund already exists for this journal.")

    def test_journal_and_company_locked_after_activation(self):
        """Lock journal_id and company_id once the fund is active or closed."""
        fund = self.env['petty.cash.fund'].create({
            'name': 'Lock Test Fund',
            'journal_id': self.petty_cash_journal.id,
            'custodian_ids': [Command.link(self.custodian_user.id)],
        })
        fund.action_activate()
        self.assertEqual(fund.state, 'active')

        with self.assertRaises(UserError):
            fund.write({'journal_id': self.petty_cash_journal_2.id})

        other_company = self.env['res.company'].create({'name': 'Second Company'})
        with self.assertRaises(UserError):
            fund.write({'company_id': other_company.id})

    def test_balance_posted_lines_and_sign(self):
        """Balance reflects only posted lines, cash in = positive, cash out = negative."""
        fund = self.env['petty.cash.fund'].create({
            'name': 'Balance Test Fund',
            'journal_id': self.petty_cash_journal.id,
            'custodian_ids': [Command.link(self.custodian_user.id)],
        })
        fund.action_activate()
        self.assertEqual(fund.balance, 0.0)

        # 1. Draft move should NOT affect balance
        bank_acc = self.company_data['default_journal_bank'].default_account_id
        draft_move = self.env['account.move'].create({
            'journal_id': self.petty_cash_journal.id,
            'line_ids': [
                Command.create({
                    'account_id': self.petty_cash_account.id,
                    'debit': 500.0,
                    'credit': 0.0,
                }),
                Command.create({
                    'account_id': bank_acc.id,
                    'debit': 0.0,
                    'credit': 500.0,
                }),
            ],
        })
        fund.invalidate_recordset(['balance'])
        self.assertEqual(fund.balance, 0.0, "Draft journal entry must not alter petty cash fund balance")

        # 2. Post move -> Cash in = positive balance (+500)
        draft_move.action_post()
        fund.invalidate_recordset(['balance'])
        self.assertEqual(fund.balance, 500.0, "Posted debit to liquidity account must result in positive balance (+500)")

        # 3. Expense payment -> Cash out = decreases balance (credit liquidity 150)
        expense_move = self.env['account.move'].create({
            'journal_id': self.petty_cash_journal.id,
            'line_ids': [
                Command.create({
                    'account_id': self.expense_account.id,
                    'debit': 150.0,
                    'credit': 0.0,
                }),
                Command.create({
                    'account_id': self.petty_cash_account.id,
                    'debit': 0.0,
                    'credit': 150.0,
                }),
            ],
        })
        expense_move.action_post()
        fund.invalidate_recordset(['balance'])
        self.assertEqual(fund.balance, 350.0, "Posted expense payout must reduce balance to +350")

    def test_close_fund_and_terminal_state(self):
        """Closing is blocked when balance is non-zero. Closed state is terminal."""
        fund = self.env['petty.cash.fund'].create({
            'name': 'Close Test Fund',
            'journal_id': self.petty_cash_journal.id,
            'custodian_ids': [Command.link(self.custodian_user.id)],
        })
        fund.action_activate()

        # Inject 100.0 balance
        move = self.env['account.move'].create({
            'journal_id': self.petty_cash_journal.id,
            'line_ids': [
                Command.create({'account_id': self.petty_cash_account.id, 'debit': 100.0}),
                Command.create({'account_id': self.expense_account.id, 'credit': 100.0}),
            ],
        })
        move.action_post()
        fund.invalidate_recordset(['balance'])
        self.assertEqual(fund.balance, 100.0)

        # Attempting to close with non-zero balance fails
        with self.assertRaises(UserError):
            fund.action_close()

        # Zero the balance
        reversal = self.env['account.move'].create({
            'journal_id': self.petty_cash_journal.id,
            'line_ids': [
                Command.create({'account_id': self.expense_account.id, 'debit': 100.0}),
                Command.create({'account_id': self.petty_cash_account.id, 'credit': 100.0}),
            ],
        })
        reversal.action_post()
        fund.invalidate_recordset(['balance'])
        self.assertEqual(fund.balance, 0.0)

        # Close succeeds
        fund.action_close()
        self.assertEqual(fund.state, 'closed')

        # Reopening is forbidden
        with self.assertRaises(UserError):
            fund.write({'state': 'draft'})
        with self.assertRaises(UserError):
            fund.write({'state': 'active'})

    def test_custodian_visibility_and_multi_company(self):
        """Custodians only see assigned funds. Record rules isolate cross-company funds."""
        fund_1 = self.env['petty.cash.fund'].create({
            'name': 'Fund 1 for Custodian 1',
            'journal_id': self.petty_cash_journal.id,
            'custodian_ids': [Command.link(self.custodian_user.id)],
        })
        fund_2 = self.env['petty.cash.fund'].create({
            'name': 'Fund 2 for Custodian 2',
            'journal_id': self.petty_cash_journal_2.id,
            'custodian_ids': [Command.link(self.custodian_user_2.id)],
        })

        # Custodian 1 sees only Fund 1
        cust1_funds = self.env['petty.cash.fund'].with_user(self.custodian_user).search([])
        self.assertIn(fund_1, cust1_funds)
        self.assertNotIn(fund_2, cust1_funds)

        # Custodian 2 sees only Fund 2
        cust2_funds = self.env['petty.cash.fund'].with_user(self.custodian_user_2).search([])
        self.assertNotIn(fund_1, cust2_funds)
        self.assertIn(fund_2, cust2_funds)

        # Manager sees both funds
        mgr_funds = self.env['petty.cash.fund'].with_user(self.manager_user).search([])
        self.assertIn(fund_1, mgr_funds)
        self.assertIn(fund_2, mgr_funds)

        # Company isolation
        comp_b = self.env['res.company'].create({'name': 'Company B Isolated'})
        journal_b = self.env['account.journal'].create({
            'name': 'Company B Cash',
            'type': 'cash',
            'code': 'PCB',
            'company_id': comp_b.id,
        })
        user_comp_b = new_test_user(
            self.env,
            login='user_comp_b',
            groups='petty_cash_management.group_petty_cash_custodian',
            company_id=comp_b.id,
            company_ids=[Command.set(comp_b.ids)],
        )
        fund_b = self.env['petty.cash.fund'].create({
            'name': 'Fund in Company B',
            'company_id': comp_b.id,
            'journal_id': journal_b.id,
            'custodian_ids': [Command.link(user_comp_b.id)],
        })

        # Approver who is NOT a custodian cannot see another custodian's fund
        appr_funds = self.env['petty.cash.fund'].with_user(self.approver_user).search([])
        self.assertNotIn(fund_1, appr_funds)
        self.assertNotIn(fund_2, appr_funds)
        with self.assertRaises(AccessError):
            fund_1.with_user(self.approver_user).read(['name'])

        # Users belonging to Company A cannot see fund_b
        mgr_company_funds = self.env['petty.cash.fund'].with_user(self.manager_user).search([])
        self.assertNotIn(fund_b, mgr_company_funds)

        # Manager of Company A cannot read, write, or unlink fund_b belonging to Company B
        with self.assertRaises(AccessError):
            fund_b.with_user(self.manager_user).read(['name'])
        with self.assertRaises(AccessError):
            fund_b.with_user(self.manager_user).write({'name': 'Cross Company Hack'})
        with self.assertRaises(AccessError):
            fund_b.with_user(self.manager_user).unlink()

    def test_custodian_only_user_access(self):
        """User with ONLY Custodian group can view assigned fund, open form view and computed balance without AccessError."""
        fund = self.env['petty.cash.fund'].create({
            'name': 'Custodian Access Test Fund',
            'journal_id': self.petty_cash_journal.id,
            'custodian_ids': [Command.link(self.custodian_user.id)],
        })

        # Ensure user does not have any accounting group
        self.assertFalse(self.custodian_user.has_group('account.group_account_readonly'))
        self.assertFalse(self.custodian_user.has_group('account.group_account_invoice'))

        # Reading the fund record works cleanly
        data = fund.with_user(self.custodian_user).read([
            'name', 'balance', 'journal_id', 'default_account_id',
            'suspense_account_id', 'outstanding_receipt_account_id',
            'outstanding_payment_account_id'
        ])[0]
        self.assertEqual(data['name'], 'Custodian Access Test Fund')
        self.assertEqual(data['balance'], 0.0)

        # Opening the fund form view (get_views) works cleanly without AccessError
        views = fund.with_user(self.custodian_user).get_views([(False, 'form')])
        self.assertTrue(views.get('views', {}).get('form'))

    def test_manager_without_accounting_cannot_read_move_lines(self):
        """Manager group does not grant access to account.move.line directly."""
        self.assertFalse(self.manager_user.has_group('account.group_account_readonly'))
        self.assertFalse(self.manager_user.has_group('account.group_account_invoice'))

        with self.assertRaises(AccessError):
            self.env['account.move.line'].with_user(self.manager_user).search([])

    def test_manager_without_accounting_group_restrictions(self):
        """Test behavior of user with ONLY Petty Cash Manager group (no accounting access)."""
        self.assertFalse(self.manager_user.has_group('account.group_account_readonly'))
        self.assertFalse(self.manager_user.has_group('account.group_account_invoice'))

        # (a) Attempting to activate a valid fund raises AccessError on account.journal
        fund = self.env['petty.cash.fund'].create({
            'name': 'Manager Fund Test',
            'journal_id': self.petty_cash_journal.id,
            'custodian_ids': [Command.link(self.custodian_user.id)],
        })
        with self.assertRaises(AccessError):
            fund.with_user(self.manager_user).action_activate()

        # (b) Attempting to pick a journal on a new fund (name_search on account.journal) raises AccessError
        with self.assertRaises(AccessError):
            self.env['account.journal'].with_user(self.manager_user).name_search('PC')

    def test_manager_with_readonly_accounting_can_select_and_activate(self):
        """User with Petty Cash Manager + account.group_account_readonly can select journal and activate fund, but cannot write to journal."""
        manager_readonly_user = new_test_user(
            self.env,
            login='petty_manager_readonly',
            name='Petty Manager Readonly',
            groups='petty_cash_management.group_petty_cash_manager,account.group_account_readonly',
            company_id=self.company.id,
            company_ids=[Command.set(self.company.ids)],
        )

        # (a) Pick a cash journal via name_search on a new fund
        journal_matches = self.env['account.journal'].with_user(manager_readonly_user).name_search('PC')
        matched_ids = [res[0] for res in journal_matches]
        self.assertIn(self.petty_cash_journal.id, matched_ids)

        # (b) Activate a valid fund
        fund = self.env['petty.cash.fund'].create({
            'name': 'Manager Readonly Fund Test',
            'journal_id': self.petty_cash_journal.id,
            'custodian_ids': [Command.link(self.custodian_user.id)],
        })
        fund.with_user(manager_readonly_user).action_activate()
        self.assertEqual(fund.state, 'active')

        # (c) In the same test assert this user gets an AccessError when writing to the journal
        with self.assertRaises(AccessError):
            fund.journal_id.with_user(manager_readonly_user).write({'name': 'Unauthorized Journal Edit'})


    def test_category_branch_and_cross_company(self):
        """Test category company validation: parent-shared accounts allowed for branch, unrelated rejected."""
        parent_comp = self.company
        branch_comp = self.env['res.company'].create({
            'name': 'Branch Company',
            'parent_id': parent_comp.id,
        })
        unrelated_comp = self.env['res.company'].create({'name': 'Unrelated Company'})

        # Shared account on parent company
        parent_expense_account = self.env['account.account'].create({
            'name': 'Parent Shared Expense',
            'code': '601991',
            'account_type': 'expense',
            'company_ids': [Command.link(parent_comp.id)],
        })

        # Unrelated company expense account
        unrelated_expense_account = self.env['account.account'].create({
            'name': 'Unrelated Expense',
            'code': '601992',
            'account_type': 'expense',
            'company_ids': [Command.link(unrelated_comp.id)],
        })

        # 1. Branch category using parent-shared account: SUCCEEDS
        branch_category = self.env['petty.cash.category'].create({
            'name': 'Branch Supplies',
            'company_id': branch_comp.id,
            'expense_account_id': parent_expense_account.id,
        })
        self.assertTrue(branch_category)

        # 2. Branch category using unrelated company account: REJECTED by _check_company_auto
        with self.assertRaises(UserError):
            self.env['petty.cash.category'].create({
                'name': 'Invalid Cross-Company Category',
                'company_id': branch_comp.id,
                'expense_account_id': unrelated_expense_account.id,
            })

    def test_unlink_restrictions(self):
        """Only draft funds can be deleted."""
        fund = self.env['petty.cash.fund'].create({
            'name': 'Unlink Test Fund',
            'journal_id': self.petty_cash_journal.id,
            'custodian_ids': [Command.link(self.custodian_user.id)],
        })
        fund.action_activate()
        with self.assertRaises(UserError):
            fund.unlink()

        # Draft fund can be deleted
        draft_fund = self.env['petty.cash.fund'].create({
            'name': 'Draft Unlink Fund',
            'journal_id': self.petty_cash_journal_2.id,
            'custodian_ids': [Command.link(self.custodian_user.id)],
        })
        draft_fund_id = draft_fund.id
        draft_fund.unlink()
        self.assertFalse(self.env['petty.cash.fund'].browse(draft_fund_id).exists())

    def test_custodian_company_constraint(self):
        """Custodian must belong to fund company."""
        comp_b = self.env['res.company'].create({'name': 'Company B Other'})
        user_other_comp = new_test_user(
            self.env,
            login='user_only_comp_b',
            groups='petty_cash_management.group_petty_cash_custodian',
            company_id=comp_b.id,
            company_ids=[Command.set(comp_b.ids)],
        )

        with self.assertRaises(ValidationError):
            self.env['petty.cash.fund'].create({
                'name': 'Invalid Custodian Fund',
                'company_id': self.company.id,
                'journal_id': self.petty_cash_journal.id,
                'custodian_ids': [Command.link(user_other_comp.id)],
            })
