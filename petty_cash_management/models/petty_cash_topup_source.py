from odoo import api, fields, models, _
from odoo.exceptions import ValidationError


class PettyCashTopupSource(models.Model):
    _name = 'petty.cash.topup.source'
    _description = 'Petty Cash Top-up Source'
    _order = 'name'
    _check_company_auto = True

    name = fields.Char(string='Source Name', required=True)
    company_id = fields.Many2one(
        comodel_name='res.company',
        string='Company',
        required=True,
        default=lambda self: self.env.company,
        index=True,
    )
    account_id = fields.Many2one(
        comodel_name='account.account',
        string='Source Account',
        required=True,
        check_company=True,
    )
    active = fields.Boolean(string='Active', default=True)

    # Constraints
    _name_company_uniq = models.Constraint(
        'UNIQUE(name, company_id)',
        'Source name must be unique per company.',
    )

    @api.constrains('account_id')
    def _check_account_type(self):
        for source in self:
            if source.account_id and source.account_id.account_type:
                if source.account_id.account_type.startswith(('expense', 'income')):
                    raise ValidationError(_("The source account cannot be an expense or income account."))

    @api.constrains('account_id', 'company_id')
    def _check_account_company(self):
        for source in self:
            if source.account_id and source.company_id:
                if source.company_id not in source.account_id.company_ids:
                    raise ValidationError(_("The source account must belong to the same company as the top-up source."))
