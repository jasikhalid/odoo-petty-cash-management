from odoo import fields, models, _
from odoo.exceptions import UserError


class PettyCashVoucherReasonWizard(models.TransientModel):
    _name = 'petty.cash.voucher.reason.wizard'
    _description = 'Petty Cash Voucher Reason Wizard'

    voucher_id = fields.Many2one('petty.cash.voucher', string='Voucher', required=True, readonly=True)
    action_type = fields.Selection([
        ('reject', 'Reject'),
        ('cancel', 'Cancel'),
    ], string='Action', required=True, readonly=True)
    reason = fields.Text(string='Reason', required=True)

    def action_confirm(self):
        self.ensure_one()
        if not self.reason or not self.reason.strip():
            raise UserError(_("Please provide a valid reason."))
        clean_reason = self.reason.strip()
        if self.action_type == 'reject':
            return self.voucher_id._action_reject_with_reason(clean_reason)
        elif self.action_type == 'cancel':
            return self.voucher_id._action_cancel_with_reason(clean_reason)
