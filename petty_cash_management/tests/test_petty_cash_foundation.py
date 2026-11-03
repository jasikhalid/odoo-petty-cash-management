import psycopg2
from odoo import Command, fields
from odoo.exceptions import AccessError, UserError, ValidationError
from odoo.tests import tagged, new_test_user
from odoo.tools import mute_logger
from odoo.tools.safe_eval import safe_eval
from .common import PettyCashTestCommon


@tagged('post_install', '-at_install')
class TestPettyCashFoundation(PettyCashTestCommon):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()

        cls.petty_cash_journal = cls.journal
        cls.outstanding_receipt_account = cls.outstanding_receipts_account
        cls.outstanding_payment_account = cls.outstanding_payments_account

        # Category expense account
        cls.expense_account = cls.env['account.account'].create({
            'name': 'Petty Cash Office Expenses',
            'code': '601099',
            'account_type': 'expense',
            'company_ids': [Command.link(cls.company.id)],
        })

        # Secondary cash journal for multi-fund tests
        cls.petty_cash_account_2 = cls.env['account.account'].create({
            'name': 'Petty Cash Liquidity Account 2',
            'code': '101089',
            'account_type': 'asset_cash',
            'company_ids': [Command.link(cls.company.id)],
        })
        cls.petty_cash_journal_2 = cls._create_cash_journal(
            name='Branch Cash Journal',
            code='PC2',
            account=cls.petty_cash_account_2,
        )

        # Secondary custodian user
        cls.custodian_user_2 = new_test_user(
            cls.env,
            login='custodian_2',
            name='Custodian Two',
            groups='petty_cash_management.group_petty_cash_custodian',
            company_id=cls.company.id,
            company_ids=[Command.set(cls.company.ids)],
        )

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

        # Custodian who is NOT in custodian_ids cannot see or read another custodian's fund
        c2_funds = self.env['petty.cash.fund'].with_user(self.custodian_user_2).search([])
        self.assertNotIn(fund_1, c2_funds)
        with self.assertRaises(AccessError):
            fund_1.with_user(self.custodian_user_2).read(['name'])

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
        """User with ONLY Custodian group:
        - sees only own funds, cannot see other funds
        - can edit non-control fields (name, notes) on own fund
        - cannot edit control fields (UserError)
        - cannot create or activate a fund
        - cannot see Configuration or To Approve menus
        """
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

        # 1. Custodian can edit non-control fields (name, notes)
        fund.with_user(self.custodian_user).write({
            'name': 'Updated Fund Name by Custodian',
            'notes': 'Custodian added fund notes',
        })
        self.assertEqual(fund.name, 'Updated Fund Name by Custodian')
        self.assertEqual(fund.notes, 'Custodian added fund notes')

        # 2. Custodian CANNOT edit control fields (UserError)
        control_tests = [
            ('custodian_ids', [Command.clear()]),
            ('journal_id', self.petty_cash_journal.id),
            ('company_id', self.company.id),
            ('state', 'active'),
            ('active', False),
        ]
        for fname, val in control_tests:
            with self.assertRaises(UserError, msg=f"Custodian writing {fname} directly should raise UserError"):
                fund.with_user(self.custodian_user).write({fname: val})

        # 3. Custodian CANNOT create a fund (AccessError)
        with self.assertRaises(AccessError):
            self.env['petty.cash.fund'].with_user(self.custodian_user).create({
                'name': 'Unauthorized Custodian Fund',
                'journal_id': self.petty_cash_journal.id,
            })

        # 4. Custodian CANNOT activate a fund (UserError)
        with self.assertRaises(UserError):
            fund.with_user(self.custodian_user).action_activate()

        # 5. Custodian CANNOT see Configuration or To Approve menus
        menu_to_approve = self.env.ref('petty_cash_management.menu_petty_cash_voucher_to_approve')
        menu_config = self.env.ref('petty_cash_management.menu_petty_cash_config')
        self.assertIn(self.env.ref('petty_cash_management.group_petty_cash_manager'), menu_to_approve.group_ids)
        self.assertIn(self.env.ref('petty_cash_management.group_petty_cash_manager'), menu_config.group_ids)
        self.assertFalse(self.custodian_user.has_group('petty_cash_management.group_petty_cash_manager'))

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

    def test_company_level_thresholds_configuration(self):
        """Company-level controls apply to all funds; old fields removed from fund and views; 0 disables; negative rejected."""
        # 1. petty.cash.fund no longer has the three fields
        fund_fields = self.env['petty.cash.fund'].fields_get()
        self.assertNotIn('low_balance_alert', fund_fields)
        self.assertNotIn('per_voucher_limit', fund_fields)
        self.assertNotIn('approval_threshold', fund_fields)

        # 2. Fund form view contains none of them
        arch = self.env['petty.cash.fund'].get_view(view_type='form')['arch']
        self.assertNotIn('low_balance_alert', arch)
        self.assertNotIn('per_voucher_limit', arch)
        self.assertNotIn('approval_threshold', arch)

        # 3. Negative values rejected on res.company
        with self.assertRaises(ValidationError):
            self.company.write({'petty_cash_low_balance_threshold': -10.0})
        with self.assertRaises(ValidationError):
            self.company.write({'petty_cash_per_expense_limit': -5.0})
        with self.assertRaises(ValidationError):
            self.company.write({'petty_cash_approval_threshold': -1.0})

        # 4. Changing company value affects all funds of that company
        fund1 = self.env['petty.cash.fund'].create({
            'name': 'Fund Alpha',
            'journal_id': self.petty_cash_journal.id,
            'custodian_ids': [Command.link(self.custodian_user.id)],
        })
        fund1.action_activate()

        fund2 = self.env['petty.cash.fund'].create({
            'name': 'Fund Beta',
            'journal_id': self.petty_cash_journal_2.id,
            'custodian_ids': [Command.link(self.custodian_user.id)],
        })
        fund2.action_activate()

        self.company.write({'petty_cash_low_balance_threshold': 100.0})
        # Both funds have 0 balance initially, so both are low balance
        fund1._compute_is_low_balance()
        fund2._compute_is_low_balance()
        self.assertTrue(fund1.is_low_balance)
        self.assertTrue(fund2.is_low_balance)

        # 5. 0 disables low balance alert
        self.company.write({'petty_cash_low_balance_threshold': 0.0})
        fund1._compute_is_low_balance()
        fund2._compute_is_low_balance()
        self.assertFalse(fund1.is_low_balance)
        self.assertFalse(fund2.is_low_balance)

        # 6. A second company is unaffected
        comp_b = self.env['res.company'].create({'name': 'Company B Settings Test'})
        self.company.write({'petty_cash_low_balance_threshold': 200.0})
        comp_b.write({'petty_cash_low_balance_threshold': 50.0})
        self.assertEqual(self.company.petty_cash_low_balance_threshold, 200.0)
        self.assertEqual(comp_b.petty_cash_low_balance_threshold, 50.0)

        # 7. Custodian can read vouchers/funds normally and cannot write company settings
        fund1.with_user(self.custodian_user).read(['name', 'balance', 'state'])
        with self.assertRaises(AccessError):
            self.company.with_user(self.custodian_user).write({'petty_cash_approval_threshold': 300.0})
        with self.assertRaises(AccessError):
            self.env['res.config.settings'].with_user(self.custodian_user).create({
                'petty_cash_approval_threshold': 300.0,
            })

    def test_res_config_settings_petty_cash_block(self):
        """Admin can configure petty cash settings via res.config.settings."""
        settings = self.env['res.config.settings'].create({
            'petty_cash_low_balance_threshold': 75.0,
            'petty_cash_per_expense_limit': 600.0,
            'petty_cash_approval_threshold': 120.0,
        })
        settings.execute()
        self.assertEqual(self.company.petty_cash_low_balance_threshold, 75.0)
        self.assertEqual(self.company.petty_cash_per_expense_limit, 600.0)
        self.assertEqual(self.company.petty_cash_approval_threshold, 120.0)

    def test_petty_cash_configuration_settings_menu_and_access(self):
        """Settings menu configuration action and multi-tier role menu visibility."""
        menu_config = self.env.ref('petty_cash_management.menu_petty_cash_config')
        menu_settings = self.env.ref('petty_cash_management.menu_petty_cash_settings')
        cat_menu = self.env.ref('petty_cash_management.menu_petty_cash_category')
        source_menu = self.env.ref('petty_cash_management.menu_petty_cash_topup_source')

        # 1. Structure and groups of Settings menu and Configuration parent
        self.assertEqual(menu_settings.parent_id, menu_config)
        self.assertEqual(menu_settings.sequence, 5)
        self.assertEqual(menu_settings.group_ids, self.env.ref('base.group_system'))
        self.assertEqual(
            set(menu_config.group_ids.ids),
            {self.env.ref('petty_cash_management.group_petty_cash_manager').id, self.env.ref('base.group_system').id},
        )

        action = menu_settings.action
        self.assertEqual(action.res_model, 'res.config.settings')
        self.assertEqual(action.view_mode, 'form')
        self.assertEqual(safe_eval(action.context), {'module': 'petty_cash_management', 'bin_size': False})

        # 2. Administrator without Petty Cash Manager group
        admin_no_manager = new_test_user(
            self.env,
            login='admin_without_petty_cash_manager',
            name='Admin Without Petty Cash Manager',
            groups='base.group_system',
            company_id=self.company.id,
            company_ids=[Command.set(self.company.ids)],
        )
        self.assertFalse(admin_no_manager.has_group('petty_cash_management.group_petty_cash_manager'))

        config_menus = menu_config | menu_settings | cat_menu | source_menu
        visible_admin = config_menus.with_user(admin_no_manager)._filter_visible_menus()
        self.assertIn(menu_config, visible_admin)
        self.assertIn(menu_settings, visible_admin)
        self.assertNotIn(cat_menu, visible_admin)
        self.assertNotIn(source_menu, visible_admin)

        # Admin saves the three company values via settings form
        settings = self.env['res.config.settings'].with_user(admin_no_manager).create({
            'petty_cash_low_balance_threshold': 85.0,
            'petty_cash_per_expense_limit': 650.0,
            'petty_cash_approval_threshold': 130.0,
        })
        settings.execute()
        self.assertEqual(self.company.petty_cash_low_balance_threshold, 85.0)
        self.assertEqual(self.company.petty_cash_per_expense_limit, 650.0)
        self.assertEqual(self.company.petty_cash_approval_threshold, 130.0)

        # 3. Petty Cash Manager who is not an administrator sees Categories and Top-up Sources, but not Settings
        visible_mgr = config_menus.with_user(self.manager_user)._filter_visible_menus()
        self.assertIn(menu_config, visible_mgr)
        self.assertNotIn(menu_settings, visible_mgr)
        self.assertIn(cat_menu, visible_mgr)
        self.assertIn(source_menu, visible_mgr)

        # 4. Custodian sees none of Configuration
        visible_cust = config_menus.with_user(self.custodian_user)._filter_visible_menus()
        self.assertNotIn(menu_config, visible_cust)
        self.assertNotIn(menu_settings, visible_cust)
        self.assertNotIn(cat_menu, visible_cust)
        self.assertNotIn(source_menu, visible_cust)

    def test_analytic_account_on_fund_propagation_and_security(self):
        """Manager sets analytic account on fund; voucher propagates it to move line; custodian cannot change it."""
        analytic_plan = self.env['account.analytic.plan'].create({'name': 'Petty Cash Test Plan'})
        analytic_account = self.env['account.analytic.account'].create({
            'name': 'Branch Operations Analytic',
            'plan_id': analytic_plan.id,
            'company_id': self.company.id,
        })
        other_analytic_account = self.env['account.analytic.account'].create({
            'name': 'Other Analytic',
            'plan_id': analytic_plan.id,
            'company_id': self.company.id,
        })

        fund = self.env['petty.cash.fund'].create({
            'name': 'Analytic Propagation Fund',
            'journal_id': self.petty_cash_journal.id,
            'custodian_ids': [Command.link(self.custodian_user.id)],
            'company_id': self.company.id,
        })
        fund.action_activate()

        # 1. Manager sets analytic account on active fund
        fund.with_user(self.manager_user).write({'analytic_account_id': analytic_account.id})
        self.assertEqual(fund.analytic_account_id, analytic_account)

        # 2. Seed fund balance with a posted cash entry
        bank_acc = self.company_data['default_account_assets']
        topup_move = self.env['account.move'].create({
            'journal_id': self.petty_cash_journal.id,
            'date': fields.Date.today(),
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
        topup_move.action_post()

        # 3. Category without analytic account
        category = self.env['petty.cash.category'].create({
            'name': 'Analytic Supplies Category',
            'expense_account_id': self.expense_account.id,
            'company_id': self.company.id,
            'receipt_required': False,
        })

        # 4. Voucher submitted below approval threshold auto-posts and carries fund analytic account
        self.company.write({'petty_cash_approval_threshold': 0.0})
        voucher = self.env['petty.cash.voucher'].with_user(self.custodian_user).create({
            'fund_id': fund.id,
            'category_id': category.id,
            'description': 'Office Stationary Analytic',
            'amount': 45.0,
            'payee_type': 'other',
            'payee_name': 'Stationery Store',
        })
        voucher.action_submit()
        self.assertEqual(voucher.state, 'posted')
        self.assertTrue(voucher.move_id)

        expense_line = voucher.move_id.with_env(self.env).line_ids.filtered(lambda l: l.account_id == category.expense_account_id)
        self.assertEqual(expense_line.analytic_distribution, {str(analytic_account.id): 100.0})

        # 5. Custodian cannot modify analytic_account_id on fund (control field protection)
        with self.assertRaises(UserError) as cm:
            fund.with_user(self.custodian_user).write({'analytic_account_id': other_analytic_account.id})
        self.assertIn("Custodians cannot modify control fields", str(cm.exception))
