from odoo import fields, models


class ResConfigSettings(models.TransientModel):
    _inherit = 'res.config.settings'

    petty_cash_low_balance_threshold = fields.Monetary(
        related='company_id.petty_cash_low_balance_threshold',
        readonly=False,
        string='Low Balance Alert Threshold',
        help="Alert threshold for funds. 0 disables the alert.",
    )
    petty_cash_per_expense_limit = fields.Monetary(
        related='company_id.petty_cash_per_expense_limit',
        readonly=False,
        string='Per-Expense Limit',
        help="Maximum single expense payout limit. 0 disables the limit.",
    )
    petty_cash_approval_threshold = fields.Monetary(
        related='company_id.petty_cash_approval_threshold',
        readonly=False,
        string='Approval Threshold',
        help="Expenses above this amount require manager approval. 0 disables approval requirement (voucher posts on submit).",
    )

