from odoo import Command
from odoo.addons.account.tests.common import AccountTestInvoicingCommon
from odoo.tests import new_test_user


class PettyCashTestCommon(AccountTestInvoicingCommon):
    """Shared test setup for Petty Cash Management tests."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.env.user.write({
            'group_ids': [
                Command.link(cls.env.ref('petty_cash_management.group_petty_cash_manager').id),
                Command.link(cls.env.ref('hr.group_hr_user').id),
            ],
        })
        cls.company = cls.env.company
        cls.currency = cls.company.currency_id

        # Cash Journal Accounts
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

        # Cash Journal
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

        # Core Test Users
        cls.custodian_user = new_test_user(
            cls.env,
            login='voucher_custodian',
            name='Voucher Custodian',
            groups='petty_cash_management.group_petty_cash_custodian',
            company_id=cls.company.id,
            company_ids=[Command.set(cls.company.ids)],
        )
        cls.manager_user = new_test_user(
            cls.env,
            login='voucher_manager',
            name='Voucher Manager',
            groups='petty_cash_management.group_petty_cash_manager',
            company_id=cls.company.id,
            company_ids=[Command.set(cls.company.ids)],
        )
