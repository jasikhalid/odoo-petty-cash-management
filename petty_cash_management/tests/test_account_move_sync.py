from odoo import Command, fields
from odoo.exceptions import UserError
from odoo.tests import tagged, new_test_user
from odoo.addons.petty_cash_management.tests.common import PettyCashTestCommon


@tagged('post_install', '-at_install')
class TestAccountMoveSync(PettyCashTestCommon):
    """Test accounting synchronization: button_draft, button_cancel, reverse, re-post."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()

        # Manager needs invoice group to post top-ups and test our cancel flow
        cls.manager_user.write({
            'group_ids': [Command.link(cls.env.ref('account.group_account_invoice').id)],
        })

        # Pure Accountant user: Billing rights ONLY, NO petty cash groups
        cls.accountant_user = new_test_user(
            cls.env,
            login='pure_accountant',
            name='Pure Accountant',
            groups='account.group_account_invoice',
            company_id=cls.company.id,
            company_ids=[Command.set(cls.company.ids)],
        )

        # Expense account & category
        cls.expense_account = cls.env['account.account'].create({
            'name': 'Sync Office Supplies',
            'code': '600096',
            'account_type': 'expense',
            'company_ids': [Command.link(cls.company.id)],
        })
        cls.category = cls.env['petty.cash.category'].create({
            'name': 'Office Supplies',
            'expense_account_id': cls.expense_account.id,
            'company_id': cls.company.id,
            'receipt_required': False,
        })

        # Partner
        cls.partner = cls.env['res.partner'].create({
            'name': 'Sync Vendor',
            'company_id': cls.company.id,
        })

        # Active Fund with 1,000 USD balance
        cls.company.write({
            'petty_cash_approval_threshold': 5000.0,
            'petty_cash_per_expense_limit': 5000.0,
        })
        cls.fund = cls.env['petty.cash.fund'].create({
            'name': 'Sync Test Fund',
            'journal_id': cls.journal.id,
            'company_id': cls.company.id,
            'custodian_ids': [Command.link(cls.custodian_user.id)],
        })
        cls.fund.action_activate()

        # Top-up source & funding top-up
        cls.bank_account = cls.env['account.account'].create({
            'name': 'Main Bank Account',
            'code': '102005',
            'account_type': 'asset_cash',
            'company_ids': [Command.link(cls.company.id)],
        })
        cls.source = cls.env['petty.cash.topup.source'].create({
            'name': 'Main Bank',
            'account_id': cls.bank_account.id,
            'company_id': cls.company.id,
        })
        topup = cls.env['petty.cash.topup'].with_user(cls.manager_user).create({
            'fund_id': cls.fund.id,
            'amount': 1000.0,
            'source_id': cls.source.id,
        })
        topup.action_post()
        cls.fund.invalidate_recordset(['balance'])

    def _create_and_post_voucher(self, amount=50.0, description='Sync Voucher'):
        voucher = self.env['petty.cash.voucher'].with_user(self.custodian_user).create({
            'fund_id': self.fund.id,
            'category_id': self.category.id,
            'description': description,
            'amount': amount,
            'payee_type': 'partner',
            'partner_id': self.partner.id,
        })
        voucher.action_submit()
        self.assertEqual(voucher.state, 'posted')
        return voucher

    def test_01_voucher_reset_to_draft_by_accountant(self):
        """Accountant resets entry to draft -> voucher cancelled, balance restored, chatter note exists."""
        self.assertFalse(self.accountant_user.has_group('petty_cash_management.group_petty_cash_custodian'))
        self.assertFalse(self.accountant_user.has_group('petty_cash_management.group_petty_cash_manager'))

        voucher = self._create_and_post_voucher(amount=50.0)
        move = voucher.move_id
        self.assertEqual(move.state, 'posted')

        self.fund.invalidate_recordset(['balance'])
        bal_after_post = self.fund.balance

        # Pure accountant resets move to draft
        move.with_user(self.accountant_user).button_draft()
        self.assertEqual(move.state, 'draft')

        # Voucher is automatically cancelled
        self.assertEqual(voucher.state, 'cancelled')

        # Fund balance is restored (unposted entry lines are no longer in balance)
        self.fund.invalidate_recordset(['balance'])
        self.assertEqual(self.fund.balance, bal_after_post + 50.0)

        # Chatter note exists on voucher
        messages = voucher.message_ids.mapped('body')
        self.assertTrue(any('was reset/cancelled by' in msg for msg in messages))

    def test_02_voucher_button_cancel_by_accountant(self):
        """Accountant cancels entry via button_cancel -> voucher cancelled, balance restored."""
        voucher = self._create_and_post_voucher(amount=60.0)
        move = voucher.move_id

        self.fund.invalidate_recordset(['balance'])
        bal_after_post = self.fund.balance

        # Pure accountant cancels move
        move.with_user(self.accountant_user).button_cancel()
        self.assertEqual(move.state, 'cancel')

        # Voucher is cancelled and balance restored
        self.assertEqual(voucher.state, 'cancelled')
        self.fund.invalidate_recordset(['balance'])
        self.assertEqual(self.fund.balance, bal_after_post + 60.0)

        # Chatter note exists
        messages = voucher.message_ids.mapped('body')
        self.assertTrue(any('was reset/cancelled by' in msg for msg in messages))

    def test_03_voucher_standard_reverse_wizard(self):
        """Standard Reverse wizard on voucher entry -> voucher cancelled and linked to reversal."""
        voucher = self._create_and_post_voucher(amount=70.0)
        move = voucher.move_id

        self.fund.invalidate_recordset(['balance'])
        bal_after_post = self.fund.balance

        # Pure accountant reverses the entry via standard wizard
        wizard = self.env['account.move.reversal'].with_user(self.accountant_user).with_context(
            active_model='account.move',
            active_ids=move.ids,
        ).create({
            'reason': 'Customer returned supplies',
            'journal_id': move.journal_id.id,
        })
        wizard.reverse_moves()

        # Voucher cancelled and linked to reversal move
        self.assertEqual(voucher.state, 'cancelled')
        self.assertTrue(voucher.reversal_move_id)
        self.assertEqual(voucher.reversal_move_id.state, 'posted')

        # Balance restored
        self.fund.invalidate_recordset(['balance'])
        self.assertEqual(self.fund.balance, bal_after_post + 70.0)

        # Chatter note on voucher
        messages = voucher.message_ids.mapped('body')
        self.assertTrue(any('was reversed by' in msg for msg in messages))

    def test_04_voucher_reset_to_draft_then_repost(self):
        """Reset to draft then post again -> voucher back to posted; warning on negative balance."""
        voucher = self._create_and_post_voucher(amount=80.0)
        move = voucher.move_id

        # 1. Reset to draft -> cancelled
        move.with_user(self.accountant_user).button_draft()
        self.assertEqual(voucher.state, 'cancelled')
        self.assertFalse(voucher.reversal_move_id)

        # 2. Re-post -> voucher back to posted
        move.with_user(self.accountant_user).action_post()
        self.assertEqual(voucher.state, 'posted')

        messages = voucher.message_ids.mapped('body')
        self.assertTrue(any('re-posted by' in msg for msg in messages))

        # 3. Test negative balance warning on re-post (never blocks)
        # Drain fund balance by creating a large voucher
        drain_voucher = self._create_and_post_voucher(amount=self.fund.balance)
        self.fund.invalidate_recordset(['balance'])
        self.assertEqual(self.fund.balance, 0.0)

        # Reset drain_voucher move to draft (fund balance becomes amount)
        drain_voucher.move_id.with_user(self.accountant_user).button_draft()
        self.assertEqual(drain_voucher.state, 'cancelled')

        # Custodian creates another voucher spending the restored balance
        spender = self._create_and_post_voucher(amount=drain_voucher.amount)
        self.assertEqual(spender.state, 'posted')
        self.fund.invalidate_recordset(['balance'])
        self.assertEqual(self.fund.balance, 0.0)

        # Now accountant re-posts drain_voucher's move: balance goes negative
        drain_voucher.move_id.with_user(self.accountant_user).action_post()
        self.assertEqual(drain_voucher.state, 'posted')

        # Does NOT block, fund balance is negative, and warning is posted in chatter
        self.fund.invalidate_recordset(['balance'])
        self.assertLess(self.fund.balance, 0.0)
        messages_drain = drain_voucher.message_ids.mapped('body')
        self.assertTrue(any('balance to become negative' in msg for msg in messages_drain))

    def test_05_voucher_cancelled_via_own_cancel_button_never_reposted(self):
        """Voucher cancelled via our Cancel button (reversal) stays cancelled even if hooks run."""
        voucher = self._create_and_post_voucher(amount=45.0)

        # Manager cancels voucher through our reason wizard
        wizard = self.env['petty.cash.voucher.reason.wizard'].with_user(self.manager_user).create({
            'voucher_id': voucher.id,
            'action_type': 'cancel',
            'reason': 'Returned unused item to vendor',
        })
        wizard.action_confirm()
        self.assertEqual(voucher.state, 'cancelled')
        self.assertTrue(voucher.reversal_move_id)

        # Hooks run again (accountant resets original move or posts)
        # Idempotent: no error, no double cancel
        move = voucher.move_id
        move.with_user(self.accountant_user).button_draft()
        self.assertEqual(voucher.state, 'cancelled')

        # Accountant attempts to re-post: voucher must NEVER come back to posted!
        move.with_user(self.accountant_user).action_post()
        self.assertEqual(voucher.state, 'cancelled')

    def test_06_topup_sync_reset_cancel_reverse_repost(self):
        """Top-up sync: reset to draft, re-post, cancel, and standard reverse."""
        # 1. Post top-up
        topup = self.env['petty.cash.topup'].with_user(self.manager_user).create({
            'fund_id': self.fund.id,
            'amount': 250.0,
            'source_id': self.source.id,
        })
        topup.action_post()
        self.assertEqual(topup.state, 'posted')

        # 2. Accountant resets top-up move to draft -> cancelled
        topup.move_id.with_user(self.accountant_user).button_draft()
        self.assertEqual(topup.state, 'cancelled')
        messages = topup.message_ids.mapped('body')
        self.assertTrue(any('was reset/cancelled by' in msg for msg in messages))

        # 3. Accountant posts top-up move again -> back to posted
        topup.move_id.with_user(self.accountant_user).action_post()
        self.assertEqual(topup.state, 'posted')
        messages_repost = topup.message_ids.mapped('body')
        self.assertTrue(any('re-posted by' in msg for msg in messages_repost))

        # 4. Accountant cancels top-up move via button_cancel
        topup.move_id.with_user(self.accountant_user).button_cancel()
        self.assertEqual(topup.state, 'cancelled')

        # 5. Standard reverse wizard on a posted top-up
        topup2 = self.env['petty.cash.topup'].with_user(self.manager_user).create({
            'fund_id': self.fund.id,
            'amount': 150.0,
            'source_id': self.source.id,
        })
        topup2.action_post()
        self.assertEqual(topup2.state, 'posted')

        wizard = self.env['account.move.reversal'].with_user(self.accountant_user).with_context(
            active_model='account.move',
            active_ids=topup2.move_id.ids,
        ).create({
            'reason': 'Reversal of top-up',
            'journal_id': topup2.move_id.journal_id.id,
        })
        wizard.reverse_moves()
        self.assertEqual(topup2.state, 'cancelled')
        messages_rev = topup2.message_ids.mapped('body')
        self.assertTrue(any('was reversed by' in msg for msg in messages_rev))

    def test_07_draft_and_submitted_vouchers_not_affected(self):
        """Draft/submitted vouchers without move_id are completely unaffected by move changes."""
        draft_voucher = self.env['petty.cash.voucher'].with_user(self.custodian_user).create({
            'fund_id': self.fund.id,
            'category_id': self.category.id,
            'description': 'Draft Voucher',
            'amount': 30.0,
            'payee_type': 'partner',
            'partner_id': self.partner.id,
        })
        self.assertEqual(draft_voucher.state, 'draft')
        self.assertFalse(draft_voucher.move_id)

        self.company.write({'petty_cash_approval_threshold': 150.0})
        submitted_voucher = self.env['petty.cash.voucher'].with_user(self.custodian_user).create({
            'fund_id': self.fund.id,
            'category_id': self.category.id,
            'description': 'Submitted Voucher Above Threshold',
            'amount': 200.0,  # Above approval_threshold (150)
            'payee_type': 'partner',
            'partner_id': self.partner.id,
        })
        submitted_voucher.action_submit()
        self.assertEqual(submitted_voucher.state, 'submitted')
        self.assertFalse(submitted_voucher.move_id)

        # Create, post, and reset an unrelated journal entry
        unrelated_move = self.env['account.move'].create({
            'move_type': 'entry',
            'journal_id': self.journal.id,
            'line_ids': [
                Command.create({'name': 'L1', 'account_id': self.petty_cash_account.id, 'debit': 10.0}),
                Command.create({'name': 'L2', 'account_id': self.expense_account.id, 'credit': 10.0}),
            ],
        })
        unrelated_move.action_post()
        unrelated_move.button_draft()
        unrelated_move.button_cancel()

        # Vouchers remain untouched
        self.assertEqual(draft_voucher.state, 'draft')
        self.assertEqual(submitted_voucher.state, 'submitted')

    def test_08_multi_company_isolation(self):
        """Accounting operations in another company do not affect vouchers in this company."""
        voucher = self._create_and_post_voucher(amount=50.0)

        # Create Company B
        company_b = self.env['res.company'].create({'name': 'Company B'})
        journal_b = self.env['account.journal'].create({
            'name': 'Company B Cash Journal',
            'code': 'CSHB',
            'type': 'cash',
            'company_id': company_b.id,
        })
        account_b = self.env['account.account'].create({
            'name': 'Cash B',
            'code': '101099',
            'account_type': 'asset_cash',
            'company_ids': [Command.link(company_b.id)],
        })
        journal_b.default_account_id = account_b

        # Create move in Company B
        move_b = self.env['account.move'].with_company(company_b).create({
            'move_type': 'entry',
            'journal_id': journal_b.id,
            'company_id': company_b.id,
            'line_ids': [
                Command.create({'name': 'B1', 'account_id': account_b.id, 'debit': 15.0}),
                Command.create({'name': 'B2', 'account_id': account_b.id, 'credit': 15.0}),
            ],
        })
        move_b.action_post()
        move_b.button_draft()

        # Voucher in Company A is untouched
        self.assertEqual(voucher.state, 'posted')

    def test_09_custodian_cannot_write_state_directly(self):
        """Custodian cannot write state or workflow fields directly (UserError)."""
        voucher = self._create_and_post_voucher(amount=40.0)
        with self.assertRaises(UserError) as cm:
            voucher.with_user(self.custodian_user).write({'state': 'cancelled'})
        self.assertIn("Workflow fields cannot be modified directly", str(cm.exception))

    def test_10_non_cash_journal_move_does_not_touch_petty_cash(self):
        """Posting, drafting or cancelling a move in a non-cash journal does not touch petty cash."""
        voucher = self._create_and_post_voucher(amount=50.0)
        self.assertEqual(voucher.state, 'posted')

        bank_journal = self.company_data['default_journal_bank']
        self.assertNotEqual(bank_journal.type, 'cash')

        # Create move in non-cash (bank) journal
        move = self.env['account.move'].create({
            'move_type': 'entry',
            'journal_id': bank_journal.id,
            'date': fields.Date.context_today(self.env.user),
            'line_ids': [
                Command.create({
                    'name': 'Bank Line 1',
                    'account_id': bank_journal.default_account_id.id,
                    'debit': 100.0,
                    'credit': 0.0,
                }),
                Command.create({
                    'name': 'Bank Line 2',
                    'account_id': self.expense_account.id,
                    'debit': 0.0,
                    'credit': 100.0,
                }),
            ],
        })
        move.action_post()
        self.assertEqual(move.state, 'posted')
        self.assertEqual(voucher.state, 'posted')

        move.button_draft()
        self.assertEqual(move.state, 'draft')
        self.assertEqual(voucher.state, 'posted')

        move.button_cancel()
        self.assertEqual(move.state, 'cancel')
        self.assertEqual(voucher.state, 'posted')


