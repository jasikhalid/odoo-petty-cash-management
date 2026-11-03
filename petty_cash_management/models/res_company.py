from odoo import api, fields, models, _
from odoo.exceptions import ValidationError


class ResCompany(models.Model):
    _inherit = 'res.company'

    petty_cash_low_balance_threshold = fields.Monetary(
        string='Low Balance Alert Threshold',
        currency_field='currency_id',
        default=0.0,
        help="Alert threshold for funds. 0 disables the alert.",
    )
    petty_cash_per_expense_limit = fields.Monetary(
        string='Per-Expense Limit',
        currency_field='currency_id',
        default=0.0,
        help="Maximum single expense payout limit. 0 disables the limit.",
    )
    petty_cash_approval_threshold = fields.Monetary(
        string='Approval Threshold',
        currency_field='currency_id',
        default=0.0,
        help="Expenses above this amount require manager approval. 0 disables approval requirement (voucher posts on submit).",
    )

    @api.constrains(
        'petty_cash_low_balance_threshold',
        'petty_cash_per_expense_limit',
        'petty_cash_approval_threshold',
    )
    def _check_petty_cash_thresholds(self):
        for company in self:
            if company.currency_id.compare_amounts(company.petty_cash_low_balance_threshold, 0.0) < 0:
                raise ValidationError(_("Low Balance Alert Threshold must be greater than or equal to zero."))
            if company.currency_id.compare_amounts(company.petty_cash_per_expense_limit, 0.0) < 0:
                raise ValidationError(_("Per-Expense Limit must be greater than or equal to zero."))
            if company.currency_id.compare_amounts(company.petty_cash_approval_threshold, 0.0) < 0:
                raise ValidationError(_("Approval Threshold must be greater than or equal to zero."))

