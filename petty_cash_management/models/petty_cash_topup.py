from odoo import api, fields, models, Command, _
from odoo.exceptions import UserError, ValidationError


class PettyCashTopup(models.Model):
    _name = 'petty.cash.topup'
    _description = 'Petty Cash Top-up'
    _inherit = ['mail.thread', 'mail.activity.mixin']
    _order = 'date desc, id desc'

    WORKFLOW_FIELDS = {
        'state', 'name', 'move_id', 'posted_by_id', 'posted_date',
    }

    name = fields.Char(
        string='Reference',
        required=True,
        copy=False,
        readonly=True,
        default='New',
        tracking=True,
    )
    fund_id = fields.Many2one(
        comodel_name='petty.cash.fund',
        string='Petty Cash Fund',
        required=True,
        domain="[('state', '=', 'active')]",
        tracking=True,
    )
    company_id = fields.Many2one(
        related='fund_id.company_id',
        string='Company',
        store=True,
        readonly=True,
        index=True,
    )
    currency_id = fields.Many2one(
        related='fund_id.currency_id',
        string='Currency',
        readonly=True,
    )
    date = fields.Date(
        string='Date',
        required=True,
        default=fields.Date.context_today,
        tracking=True,
    )
    amount = fields.Monetary(
        string='Amount',
        required=True,
        currency_field='currency_id',
        tracking=True,
    )
    source_id = fields.Many2one(
        comodel_name='petty.cash.topup.source',
        string='Source',
        required=True,
        check_company=True,
        tracking=True,
    )
    notes = fields.Text(string='Notes')
    state = fields.Selection([
        ('draft', 'Draft'),
        ('posted', 'Posted'),
        ('cancelled', 'Cancelled'),
    ], string='Status', default='draft', readonly=True, tracking=True, copy=False)
    move_id = fields.Many2one(
        comodel_name='account.move',
        string='Journal Entry',
        readonly=True,
        copy=False,
        ondelete='restrict',
    )
    posted_by_id = fields.Many2one(
        comodel_name='res.users',
        string='Posted By',
        readonly=True,
        copy=False,
        tracking=True,
    )
    posted_date = fields.Datetime(
        string='Posted Date',
        readonly=True,
        copy=False,
        tracking=True,
    )

    # Constraints
    _move_id_uniq = models.Constraint(
        'UNIQUE(move_id)',
        'A journal entry can only be linked to one top-up.',
    )

    @api.constrains('amount')
    def _check_amount_positive(self):
        for topup in self:
            if topup.currency_id and topup.currency_id.compare_amounts(topup.amount, 0.0) <= 0:
                raise ValidationError(_("Top-up amount must be strictly greater than zero."))

    @api.constrains('fund_id', 'source_id')
    def _check_source_company(self):
        for topup in self:
            if topup.fund_id and topup.source_id:
                if topup.fund_id.company_id != topup.source_id.company_id:
                    raise ValidationError(_("The top-up source must belong to the same company as the fund."))

    @api.constrains('fund_id', 'source_id')
    def _check_source_account(self):
        for topup in self:
            if topup.fund_id and topup.source_id:
                if topup.fund_id.default_account_id == topup.source_id.account_id:
                    raise ValidationError(_("The source account cannot be the same as the petty cash fund default account."))

    # -------------------------------------------------------------------------
    # WORKFLOW PROTECTION
    # -------------------------------------------------------------------------

    def _workflow_write(self, vals):
        """Internal private method to update workflow fields bypassing ORM write protection."""
        return super().write(vals)

    @api.model
    def default_get(self, fields_list):
        res = super().default_get(fields_list)
        if 'fund_id' in fields_list and not res.get('fund_id'):
            active_funds = self.env['petty.cash.fund'].search([
                ('state', '=', 'active'),
                ('custodian_ids', 'in', [self.env.uid]),
            ])
            if len(active_funds) == 1:
                res['fund_id'] = active_funds.id
        return res

    @api.model_create_multi
    def create(self, vals_list):
        for vals in vals_list:
            if any(f in vals for f in (self.WORKFLOW_FIELDS - {'state', 'name'})):
                raise UserError(_("Workflow fields cannot be set at creation."))
            if 'state' in vals and vals['state'] != 'draft':
                raise UserError(_("New top-ups must be created in draft state."))
            if 'name' in vals and vals['name'] != 'New':
                raise UserError(_("Top-up reference cannot be specified at creation."))
            vals['state'] = 'draft'
            vals['name'] = 'New'
        return super().create(vals_list)

    def write(self, vals):
        if any(f in vals for f in self.WORKFLOW_FIELDS):
            raise UserError(_("Workflow fields cannot be modified directly."))
        business_fields = {'fund_id', 'date', 'amount', 'source_id', 'notes'}
        if any(f in vals for f in business_fields):
            for topup in self:
                if topup.state != 'draft':
                    raise UserError(_("Cannot modify details of a top-up that is not in draft state."))
        return super().write(vals)

    @api.ondelete(at_uninstall=False)
    def _unlink_draft_only(self):
        for topup in self:
            if topup.state != 'draft':
                raise UserError(_("Only draft top-ups can be deleted."))

    # -------------------------------------------------------------------------
    # ACTIONS
    # -------------------------------------------------------------------------

    def action_post(self):
        self.ensure_one()
        # 1. Permission checks
        if not self.env.user.has_group('petty_cash_management.group_petty_cash_manager'):
            raise UserError(_("Only a Petty Cash Manager can post top-ups."))
        if not self.env.user.has_group('account.group_account_invoice'):
            raise UserError(_("Posting a top-up creates an accounting entry and requires the Invoicing group (account.group_account_invoice)."))

        # 2. State & twice-posting check
        if self.state != 'draft':
            raise UserError(_("Only draft top-ups can be posted."))
        if self.move_id:
            raise UserError(_("This top-up already has a journal entry linked to it."))

        # 3. Model validations
        if self.fund_id.state != 'active':
            raise UserError(_("The petty cash fund must be active to post a top-up."))

        if self.currency_id.compare_amounts(self.amount, 0.0) <= 0:
            raise UserError(_("Top-up amount must be strictly greater than zero."))

        today = fields.Date.context_today(self)
        if self.date > today:
            raise UserError(_("The top-up date cannot be in the future."))

        # Check fiscal lock dates
        violated_locks = self.fund_id.company_id._get_lock_date_violations(
            self.date,
            fiscalyear=True,
            sale=False,
            purchase=False,
            tax=False,
            hard=True,
        )
        if violated_locks:
            raise UserError(_("You cannot add/modify entries prior to and inclusive of: %s.") %
                            self.env['res.company']._format_lock_dates(violated_locks))

        if self.source_id.company_id != self.fund_id.company_id:
            raise UserError(_("The top-up source must belong to the same company as the fund."))

        if self.source_id.account_id == self.fund_id.default_account_id:
            raise UserError(_("The source account cannot be the same as the petty cash fund default account."))

        # 4. Generate sequence
        seq_name = self.env['ir.sequence'].with_company(self.company_id).next_by_code('petty.cash.topup', sequence_date=self.date)
        if not seq_name:
            raise UserError(_("Sequence for petty.cash.topup not found."))

        # 5. Create account.move (NO sudo, real user context with fund company)
        line_label = _("Top-up from %(source)s", source=self.source_id.name)
        move_vals = {
            'move_type': 'entry',
            'ref': seq_name,
            'journal_id': self.fund_id.journal_id.id,
            'date': self.date,
            'company_id': self.fund_id.company_id.id,
            'currency_id': self.currency_id.id,
            'line_ids': [
                Command.create({
                    'name': line_label,
                    'account_id': self.fund_id.default_account_id.id,
                    'debit': self.amount,
                    'credit': 0.0,
                }),
                Command.create({
                    'name': line_label,
                    'account_id': self.source_id.account_id.id,
                    'debit': 0.0,
                    'credit': self.amount,
                }),
            ],
        }
        move = self.env['account.move'].with_company(self.fund_id.company_id).create(move_vals)
        move.action_post()

        # 6. Update top-up workflow fields
        self._workflow_write({
            'name': seq_name,
            'move_id': move.id,
            'state': 'posted',
            'posted_by_id': self.env.user.id,
            'posted_date': fields.Datetime.now(),
        })

        # 7. Chatter message & invalidate fund balance
        self.message_post(
            body=_("Top-up posted. Journal Entry: %(entry)s", entry=move.name),
        )
        self.fund_id.invalidate_recordset(['balance'])
        return True

    def action_cancel(self):
        self.ensure_one()
        if not self.env.user.has_group('petty_cash_management.group_petty_cash_manager'):
            raise UserError(_("Only a Petty Cash Manager can cancel top-ups."))
        if self.state == 'posted':
            raise UserError(_("A posted top-up cannot be cancelled. The accountant must reverse the journal entry in Accounting."))
        if self.state != 'draft':
            raise UserError(_("Only draft top-ups can be cancelled."))
        self._workflow_write({'state': 'cancelled'})
        self.message_post(body=_("Top-up cancelled."))
        return True

    def action_view_journal_entry(self):
        self.ensure_one()
        if not self.move_id:
            raise UserError(_("No journal entry is linked to this top-up."))
        return {
            'name': _("Journal Entry"),
            'type': 'ir.actions.act_window',
            'res_model': 'account.move',
            'res_id': self.move_id.id,
            'view_mode': 'form',
            'target': 'current',
        }
