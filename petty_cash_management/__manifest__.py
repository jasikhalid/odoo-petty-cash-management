{
    'name': 'Petty Cash Management',
    'version': '20.0.1.0.0',
    'category': 'Accounting/Accounting',
    'summary': 'Petty Cash Management with direct accounting ledger integration',
    'description': """
Petty Cash Management with direct General Ledger integration.
Connects petty cash funds directly to Odoo Cash Journals, ensuring real-time balance
accuracy without shadow ledgers or balance drift. Supports branch fund administration,
voucher disbursement, multi-level approvals, receipt verification, and fund top-ups.
    """,
    'author': 'Jasi Khalid',
    'license': 'LGPL-3',
    'depends': [
        'account',
        'account_accountant',
        'hr',
        'mail',
    ],
    'data': [
        'security/security.xml',
        'security/ir.access.csv',
        'data/ir_sequence_data.xml',
        'wizard/petty_cash_voucher_reason_wizard_views.xml',
        'views/petty_cash_fund_views.xml',
        'views/petty_cash_category_views.xml',
        'views/petty_cash_voucher_views.xml',
        'views/petty_cash_topup_source_views.xml',
        'views/petty_cash_topup_views.xml',
        'views/menus.xml',
    ],
    'installable': True,
    'application': True,
    'auto_install': False,
}
