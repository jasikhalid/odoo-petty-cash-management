from odoo import api, fields, models, _
from odoo.exceptions import UserError, ValidationError


class PettyCashFund(models.Model):
    _name = 'petty.cash.fund'
    _description = 'Petty Cash Fund'
    _inherit = ['mail.thread', 'mail.activity.mixin']
    _order = 'name'
    _check_company_auto = True

    name = fields.Char(string='Fund Name', required=True, tracking=True)
    active = fields.Boolean(default=True)
    state = fields.Selection([
        ('draft', 'Draft'),
        ('active', 'Active'),
        ('closed', 'Closed'),
    ], string='Status', default='draft', required=True, readonly=True, tracking=True, copy=False)

    company_id = fields.Many2one(
        comodel_name='res.company',
        string='Company',
        required=True,
        default=lambda self: self.env.company,
        index=True,
    )
    currency_id = fields.Many2one(
        comodel_name='res.currency',
        string='Currency',
        related='company_id.currency_id',
        readonly=True,
    )
    journal_id = fields.Many2one(
        comodel_name='account.journal',
        string='Petty Cash Journal',
        check_company=True,
        domain="[('type', '=', 'cash'), ('company_id', '=', company_id)]",
    )
    custodian_ids = fields.Many2many(
        comodel_name='res.users',
        relation='petty_cash_fund_custodian_rel',
        column1='fund_id',
        column2='user_id',
        string='Custodians',
    )

    # Read-only display of configured journal accounts
    default_account_id = fields.Many2one(
        comodel_name='account.account',
        related='journal_id.default_account_id',
        compute_sudo=True,
        string='Petty Cash Account',
        readonly=True,
    )
    suspense_account_id = fields.Many2one(
        comodel_name='account.account',
        related='journal_id.suspense_account_id',
        compute_sudo=True,
        string='Suspense Account',
        readonly=True,
    )
    outstanding_receipt_account_id = fields.Many2one(
        comodel_name='account.account',
        compute='_compute_outstanding_accounts',
        compute_sudo=True,
        string='Outstanding Receipts Account',
        readonly=True,
    )
    outstanding_payment_account_id = fields.Many2one(
        comodel_name='account.account',
        compute='_compute_outstanding_accounts',
        compute_sudo=True,
        string='Outstanding Payments Account',
        readonly=True,
    )

    balance = fields.Monetary(
        string='Current Balance',
        compute='_compute_balance',
        compute_sudo=True,
        search='_search_balance',
        currency_field='currency_id',
        store=False,
    )
    low_balance_alert = fields.Monetary(
        string='Low Balance Alert Threshold',
        currency_field='currency_id',
        help="Optional alert threshold. Fund will be flagged when balance falls below this amount.",
    )
    is_low_balance = fields.Boolean(
        string='Low Balance Alert',
        compute='_compute_is_low_balance',
        search='_search_is_low_balance',
    )
    per_voucher_limit = fields.Monetary(
        string='Per Voucher Limit',
        currency_field='currency_id',
        help="Maximum single voucher payout limit (enforced in Phase 2).",
    )
    approval_threshold = fields.Monetary(
        string='Approval Threshold',
        currency_field='currency_id',
        help="Vouchers above this amount require approver sign-off (enforced in Phase 3).",
    )
    analytic_account_id = fields.Many2one(
        comodel_name='account.analytic.account',
        string='Default Analytic Account',
        check_company=True,
    )
    voucher_count = fields.Integer(
        string='Vouchers',
        compute='_compute_voucher_count',
    )
    topup_ids = fields.One2many(
        comodel_name='petty.cash.topup',
        inverse_name='fund_id',
        string='Top-ups',
    )
    topup_count = fields.Integer(
        string='Top-ups Count',
        compute='_compute_topup_count',
    )


    # Constraints
    _journal_uniq = models.Constraint(
        'UNIQUE(journal_id)',
        'A petty cash fund already exists for this journal.',
    )

    @api.constrains('custodian_ids', 'company_id')
    def _check_custodian_company(self):
        for fund in self:
            for custodian in fund.custodian_ids:
                if fund.company_id not in custodian.company_ids:
                    raise ValidationError(_(
                        "Custodian '%(custodian)s' does not belong to company '%(company)s'.",
                        custodian=custodian.name,
                        company=fund.company_id.name,
                    ))

    @api.depends('journal_id',
                 'journal_id.company_id',
                 'journal_id.inbound_payment_method_line_ids.payment_account_id',
                 'journal_id.outbound_payment_method_line_ids.payment_account_id')
    def _compute_outstanding_accounts(self):
        for fund in self:
            if fund.journal_id:
                inbound_accounts = fund.journal_id._get_journal_inbound_outstanding_payment_accounts()
                outbound_accounts = fund.journal_id._get_journal_outbound_outstanding_payment_accounts()
                fund.outstanding_receipt_account_id = inbound_accounts[:1] if inbound_accounts else False
                fund.outstanding_payment_account_id = outbound_accounts[:1] if outbound_accounts else False
            else:
                fund.outstanding_receipt_account_id = False
                fund.outstanding_payment_account_id = False

    @api.depends('journal_id', 'journal_id.default_account_id')
    def _compute_balance(self):
        # sudo() is strictly allowed here for read-only aggregation scoped to the fund's
        # own journal and default account, because petty cash custodians do not have
        # read access to account.move.line directly.
        funds_with_account = self.filtered(lambda f: f.journal_id and f.journal_id.default_account_id)
        (self - funds_with_account).balance = 0.0
        if not funds_with_account:
            return

        domain = [
            ('journal_id', 'in', funds_with_account.journal_id.ids),
            ('account_id', 'in', funds_with_account.journal_id.default_account_id.ids),
            ('parent_state', '=', 'posted'),
        ]
        groups = self.env['account.move.line'].sudo()._read_group(
            domain=domain,
            groupby=['journal_id', 'account_id'],
            aggregates=['balance:sum'],
        )
        balance_map = {(journal.id, account.id): balance for journal, account, balance in groups}
        for fund in funds_with_account:
            key = (fund.journal_id.id, fund.journal_id.default_account_id.id)
            fund.balance = balance_map.get(key, 0.0)

    def _search_balance(self, operator, value):
        valid_operators = ('<', '<=', '>', '>=', '=', '!=')
        if operator not in valid_operators:
            raise UserError(_("Unsupported search operator '%s' for balance.", operator))

        funds = self.search([])
        matched_ids = []
        for fund in funds:
            if operator == '<' and fund.balance < value:
                matched_ids.append(fund.id)
            elif operator == '<=' and fund.balance <= value:
                matched_ids.append(fund.id)
            elif operator == '>' and fund.balance > value:
                matched_ids.append(fund.id)
            elif operator == '>=' and fund.balance >= value:
                matched_ids.append(fund.id)
            elif operator == '=' and fund.currency_id.is_zero(fund.balance - value):
                matched_ids.append(fund.id)
            elif operator == '!=' and not fund.currency_id.is_zero(fund.balance - value):
                matched_ids.append(fund.id)
        return [('id', 'in', matched_ids)]

    @api.depends('balance', 'low_balance_alert', 'state')
    def _compute_is_low_balance(self):
        for fund in self:
            fund.is_low_balance = (
                fund.state == 'active'
                and fund.low_balance_alert > 0.0
                and fund.balance < fund.low_balance_alert
            )

    def _search_is_low_balance(self, operator, value):
        if operator not in ('=', '!='):
            raise UserError(_("Unsupported search operator for low balance alert."))
        active_alert_funds = self.search([
            ('state', '=', 'active'),
            ('low_balance_alert', '>', 0.0),
        ])
        matching_ids = [fund.id for fund in active_alert_funds if fund.is_low_balance]
        is_true = (operator == '=' and value) or (operator == '!=' and not value)
        return [('id', 'in' if is_true else 'not in', matching_ids)]

    @api.ondelete(at_uninstall=False)
    def _unlink_except_non_draft(self):
        for fund in self:
            if fund.state != 'draft':
                raise UserError(_("Only draft petty cash funds can be deleted. Archive the fund instead."))

    def write(self, vals):
        # Lock journal_id and company_id once active or closed
        if ('journal_id' in vals or 'company_id' in vals) and any(fund.state != 'draft' for fund in self):
            raise UserError(_("You cannot change the journal or company of an active or closed petty cash fund."))

        # Closed state is terminal; prevent reopening
        if 'state' in vals and vals['state'] != 'closed':
            if any(fund.state == 'closed' for fund in self):
                raise UserError(_("A closed petty cash fund is terminal and cannot be reopened. Please archive it instead."))
        return super().write(vals)

    def action_activate(self):
        self.ensure_one()
        errors = []

        if not self.custodian_ids:
            errors.append(_("- At least one custodian must be assigned to the fund."))

        if not self.journal_id:
            errors.append(_("- Petty Cash Journal must be set."))
        else:
            if self.journal_id.type != 'cash':
                errors.append(_("- Journal '%(journal)s' must be of type 'Cash'.", journal=self.journal_id.display_name))
            if self.journal_id.company_id != self.company_id:
                errors.append(_("- Journal company (%(j_comp)s) must match fund company (%(f_comp)s).",
                                j_comp=self.journal_id.company_id.name,
                                f_comp=self.company_id.name))
            if not self.journal_id.default_account_id:
                errors.append(_("- Journal default account (Petty Cash account) is not set."))
            elif self.journal_id.default_account_id.account_type != 'asset_cash':
                errors.append(_("- Journal default account must be of type 'Bank and Cash' (asset_cash)."))
            if not self.journal_id.suspense_account_id:
                errors.append(_("- Journal suspense account is not set."))
            if not self.outstanding_receipt_account_id:
                errors.append(_("- Inbound payment method line has no outstanding receipts account configured."))
            if not self.outstanding_payment_account_id:
                errors.append(_("- Outbound payment method line has no outstanding payments account configured."))

        if errors:
            raise UserError(_("Cannot activate Petty Cash Fund '%(name)s':\n%(errors)s",
                              name=self.name,
                              errors='\n'.join(errors)))

        self.write({'state': 'active'})

    def _compute_voucher_count(self):
        voucher_data = self.env['petty.cash.voucher']._read_group(
            [('fund_id', 'in', self.ids)],
            groupby=['fund_id'],
            aggregates=['__count'],
        )
        counts = {fund.id: count for fund, count in voucher_data}
        for fund in self:
            fund.voucher_count = counts.get(fund.id, 0)

    def _compute_topup_count(self):
        topup_data = self.env['petty.cash.topup']._read_group(
            [('fund_id', 'in', self.ids)],
            groupby=['fund_id'],
            aggregates=['__count'],
        )
        counts = {fund.id: count for fund, count in topup_data}
        for fund in self:
            fund.topup_count = counts.get(fund.id, 0)

    def action_close(self):
        self.ensure_one()
        open_vouchers = self.env['petty.cash.voucher'].search_count([
            ('fund_id', '=', self.id),
            ('state', 'in', ('draft', 'submitted')),
        ])
        if open_vouchers:
            raise UserError(_("Cannot close a fund with draft or submitted vouchers. Please cancel or process them first."))
        if not self.currency_id.is_zero(self.balance):
            raise UserError(_(
                "A petty cash fund can only be closed when its balance is zero. Current balance is %(balance)s.",
                balance=self.currency_id.format(self.balance),
            ))
        self.write({'state': 'closed'})

    def action_view_vouchers(self):
        self.ensure_one()
        return {
            'name': _("Vouchers: %s", self.name),
            'type': 'ir.actions.act_window',
            'res_model': 'petty.cash.voucher',
            'view_mode': 'list,form',
            'domain': [('fund_id', '=', self.id)],
            'context': {'default_fund_id': self.id},
        }

    def action_open_new_topup(self):
        self.ensure_one()
        return {
            'name': _("New Top-up"),
            'type': 'ir.actions.act_window',
            'res_model': 'petty.cash.topup',
            'view_mode': 'form',
            'target': 'current',
            'context': {
                'default_fund_id': self.id,
            },
        }

    def action_view_topups(self):
        self.ensure_one()
        return {
            'name': _("Top-ups: %s", self.name),
            'type': 'ir.actions.act_window',
            'res_model': 'petty.cash.topup',
            'view_mode': 'list,form',
            'domain': [('fund_id', '=', self.id)],
            'context': {'default_fund_id': self.id},
        }

    def action_view_journal_items(self):
        self.ensure_one()
        return {
            'type': 'ir.actions.act_window',
            'name': _('Journal Items: %s', self.name),
            'res_model': 'account.move.line',
            'view_mode': 'list,form',
            'domain': [
                ('journal_id', '=', self.journal_id.id),
                ('account_id', '=', self.journal_id.default_account_id.id),
                ('parent_state', '=', 'posted'),
            ],
            'context': {
                'create': False,
                'edit': False,
                'delete': False,
            },
        }
