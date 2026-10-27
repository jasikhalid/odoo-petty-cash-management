from odoo import api, fields, models, _
from odoo.exceptions import ValidationError


class PettyCashCategory(models.Model):
    _name = 'petty.cash.category'
    _description = 'Petty Cash Category'
    _order = 'name'
    _check_company_auto = True

    name = fields.Char(string='Category Name', required=True)
    active = fields.Boolean(string='Active', default=True)
    company_id = fields.Many2one(
        comodel_name='res.company',
        string='Company',
        required=True,
        default=lambda self: self.env.company,
        index=True,
    )
    expense_account_id = fields.Many2one(
        comodel_name='account.account',
        string='Expense Account',
        required=True,
        check_company=True,
        domain="[('account_type', 'in', ('expense', 'expense_other', 'expense_direct_cost'))]",
    )
    tax_ids = fields.Many2many(
        comodel_name='account.tax',
        relation='petty_cash_category_tax_rel',
        column1='category_id',
        column2='tax_id',
        string='Default Taxes',
        check_company=True,
        domain="[('type_tax_use', '=', 'purchase')]",
    )
    receipt_required = fields.Boolean(
        string='Receipt Required',
        default=True,
        help="If enabled, attaching a receipt is mandatory for vouchers in this category.",
    )
    analytic_account_id = fields.Many2one(
        comodel_name='account.analytic.account',
        string='Default Analytic Account',
        check_company=True,
    )

    # Constraints
    _name_company_uniq = models.Constraint(
        'UNIQUE(name, company_id)',
        'Category name must be unique per company.',
    )

    @api.constrains('expense_account_id')
    def _check_expense_account(self):
        allowed_types = ('expense', 'expense_other', 'expense_direct_cost')
        for category in self:
            if category.expense_account_id and category.expense_account_id.account_type not in allowed_types:
                raise ValidationError(_(
                    "Expense account '%(account)s' must be of type Expenses, Other Expenses, or Cost of Revenue.",
                    account=category.expense_account_id.display_name,
                ))
