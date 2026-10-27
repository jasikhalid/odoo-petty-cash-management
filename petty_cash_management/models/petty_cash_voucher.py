from odoo import api, fields, models, Command, _
from odoo.exceptions import AccessError, LockError, UserError, ValidationError
from odoo.tools.misc import formatLang


class PettyCashVoucher(models.Model):
    _name = 'petty.cash.voucher'
    _description = 'Petty Cash Voucher'
    _inherit = ['mail.thread', 'mail.activity.mixin']
    _order = 'date desc, id desc'
    _check_company_auto = True

    _unique_move_id = models.Constraint(
        'UNIQUE(move_id)',
        "A journal entry is already linked to this voucher.",
    )
    _unique_name = models.UniqueIndex(
        "(name, company_id) WHERE name != 'New'",
        "A voucher with this number already exists in this company.",
    )

    WORKFLOW_FIELDS = {
        'state', 'name', 'move_id', 'reversal_move_id',
        'submitted_by_id', 'submitted_date', 'approved_by_id', 'approved_date',
        'rejection_reason', 'cancel_reason',
    }

    name = fields.Char(
        string='Voucher Number',
        required=True,
        copy=False,
        readonly=True,
        default='New',
        tracking=True,
    )
    fund_id = fields.Many2one(
        'petty.cash.fund',
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
    category_id = fields.Many2one(
        'petty.cash.category',
        string='Expense Category',
        required=True,
        check_company=True,
        tracking=True,
    )
    description = fields.Char(
        string='Description',
        required=True,
        tracking=True,
    )
    amount = fields.Monetary(
        string='Amount',
        required=True,
        currency_field='currency_id',
        tracking=True,
    )
    payee_type = fields.Selection([
        ('partner', 'Partner'),
        ('employee', 'Employee'),
        ('other', 'Other / Free Text'),
    ], string='Payee Type', required=True, default='partner', tracking=True)

    partner_id = fields.Many2one(
        'res.partner',
        string='Partner',
        check_company=True,
        tracking=True,
    )
    employee_id = fields.Many2one(
        'hr.employee',
        string='Employee',
        check_company=True,
        tracking=True,
    )
    payee_name = fields.Char(
        string='Payee Name',
        tracking=True,
    )
    analytic_account_id = fields.Many2one(
        'account.analytic.account',
        string='Analytic Account',
        check_company=True,
    )
    state = fields.Selection([
        ('draft', 'Draft'),
        ('submitted', 'Submitted'),
        ('posted', 'Posted'),
        ('rejected', 'Rejected'),
        ('cancelled', 'Cancelled'),
    ], string='Status', required=True, default='draft', readonly=True, tracking=True)

    rejection_reason = fields.Text(string='Rejection Reason', readonly=True, copy=False)
    cancel_reason = fields.Text(string='Cancel Reason', readonly=True, copy=False)

    submitted_by_id = fields.Many2one('res.users', string='Submitted By', readonly=True, copy=False)
    submitted_date = fields.Datetime(string='Submitted On', readonly=True, copy=False)
    approved_by_id = fields.Many2one('res.users', string='Approved By', readonly=True, copy=False)
    approved_date = fields.Datetime(string='Approved On', readonly=True, copy=False)

    move_id = fields.Many2one(
        'account.move',
        string='Journal Entry',
        readonly=True,
        copy=False,
        ondelete='restrict',
    )
    reversal_move_id = fields.Many2one(
        'account.move',
        string='Reversal Entry',
        readonly=True,
        copy=False,
        ondelete='restrict',
    )
    attachment_count = fields.Integer(
        string='Receipts Count',
        compute='_compute_attachment_count',
    )

    def _compute_attachment_count(self):
        attachment_data = self.env['ir.attachment']._read_group(
            [('res_model', '=', self._name), ('res_id', 'in', self.ids)],
            groupby=['res_id'],
            aggregates=['__count'],
        )
        counts = {res_id: count for res_id, count in attachment_data}
        for voucher in self:
            voucher.attachment_count = counts.get(voucher.id, 0)

    @api.constrains('amount')
    def _check_amount_positive(self):
        for voucher in self:
            if voucher.currency_id.is_zero(voucher.amount) or voucher.currency_id.compare_amounts(voucher.amount, 0.0) < 0:
                raise ValidationError(_("The voucher amount must be strictly greater than zero."))

    @api.constrains('fund_id', 'category_id')
    def _check_fund_category_company(self):
        for voucher in self:
            if voucher.fund_id and voucher.category_id:
                if voucher.fund_id.company_id != voucher.category_id.company_id:
                    raise ValidationError(_("The selected category must belong to the same company as the petty cash fund."))

    def _workflow_write(self, vals):
        """Internal private method to update workflow fields bypassing ORM write protection.
        Cannot be called over RPC as it starts with an underscore.
        """
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
                raise UserError(_("New vouchers must be created in draft state."))
            if 'name' in vals and vals['name'] != 'New':
                raise UserError(_("Voucher number cannot be specified at creation."))
            vals['state'] = 'draft'
            vals['name'] = 'New'
        return super().create(vals_list)

    def write(self, vals):
        if any(f in vals for f in self.WORKFLOW_FIELDS):
            raise UserError(_("Workflow fields cannot be modified directly."))
        business_fields = {
            'amount', 'fund_id', 'category_id', 'date', 'description',
            'payee_type', 'partner_id', 'employee_id', 'payee_name',
            'analytic_account_id',
        }
        if any(f in vals for f in business_fields):
            for voucher in self:
                if voucher.state != 'draft':
                    raise UserError(_("Cannot modify details of a voucher that is not in draft state."))
        return super().write(vals)

    @api.ondelete(at_uninstall=False)
    def _unlink_draft_only(self):
        for voucher in self:
            if voucher.state != 'draft':
                raise UserError(_("Only draft vouchers can be deleted. Please cancel or archive instead."))

    # -------------------------------------------------------------------------
    # WORKFLOW ACTIONS
    # -------------------------------------------------------------------------

    def action_submit(self):
        self.ensure_one()
        # Explicit permission check BEFORE any action
        is_manager = self.env.user.has_group('petty_cash_management.group_petty_cash_manager')
        is_custodian = self.env.user in self.fund_id.custodian_ids
        if not (is_manager or is_custodian):
            raise UserError(_("Only an assigned custodian of this fund or a Petty Cash Manager can submit this voucher."))

        if self.state != 'draft':
            raise UserError(_("Only draft vouchers can be submitted."))

        # 1. Validations
        if self.fund_id.state != 'active':
            raise UserError(_("Cannot submit a voucher against an inactive or closed fund."))

        today = fields.Date.context_today(self)
        if self.date > today:
            raise UserError(_("Voucher date cannot be in the future."))

        if self.currency_id.is_zero(self.amount) or self.currency_id.compare_amounts(self.amount, 0.0) < 0:
            raise UserError(_("The voucher amount must be strictly greater than zero."))
        if self.category_id.company_id != self.fund_id.company_id:
            raise UserError(_("The selected category and fund must belong to the same company."))

        # 2. Tax rule (Phase 2 constraint)
        if self.category_id.tax_ids:
            raise UserError(_("Taxes are not supported in Phase 2. Category '%s' has taxes configured.") % self.category_id.name)

        # 3. Per-voucher limit (hard stop)
        if self.fund_id.per_voucher_limit and self.currency_id.compare_amounts(self.amount, self.fund_id.per_voucher_limit) > 0:
            raise UserError(_("The amount (%s) exceeds the fund's per-voucher limit of %s.") % (
                formatLang(self.env, self.amount, currency_obj=self.currency_id),
                formatLang(self.env, self.fund_id.per_voucher_limit, currency_obj=self.currency_id),
            ))

        # 4. Receipt requirement
        if self.category_id.receipt_required:
            if self.attachment_count <= 0:
                raise UserError(_("A receipt attachment is required for this voucher."))

        # 5. Payee validation
        if self.payee_type == 'partner':
            if not self.partner_id:
                raise UserError(_("Partner is required when payee type is Partner."))
        elif self.payee_type == 'employee':
            if not self.employee_id:
                raise UserError(_("Employee is required when payee type is Employee."))
            if not self.employee_id.work_contact_id:
                raise UserError(_("The employee '%s' has no work contact partner configured.") % self.employee_id.name)
        elif self.payee_type == 'other':
            if not self.payee_name or not self.payee_name.strip():
                raise UserError(_("Payee Name is required when payee type is Other."))

        # 6. Assign sequence ONLY after all validations pass (avoids sequence gaps)
        workflow_vals = {
            'submitted_by_id': self.env.user.id,
            'submitted_date': fields.Datetime.now(),
        }
        if not self.name or self.name == 'New':
            seq_val = self.env['ir.sequence'].with_company(self.company_id).next_by_code('petty.cash.voucher', sequence_date=self.date)
            if not seq_val:
                raise UserError(_("Sequence for petty cash voucher not found for company %s.") % self.company_id.name)
            workflow_vals['name'] = seq_val

        # 7. Check approval requirement
        needs_approval = bool(
            self.fund_id.approval_threshold
            and self.currency_id.compare_amounts(self.amount, self.fund_id.approval_threshold) > 0
        )
        if needs_approval:
            workflow_vals['state'] = 'submitted'
            self._workflow_write(workflow_vals)
            self.message_post(body=_("Voucher %s submitted for approval (Amount: %s).") % (
                self.name,
                formatLang(self.env, self.amount, currency_obj=self.currency_id),
            ))
        else:
            self._workflow_write(workflow_vals)
            self._post_to_ledger()

    def action_approve(self):
        self.ensure_one()
        self._check_approver_authorization()
        if self.state != 'submitted':
            raise UserError(_("Only submitted vouchers can be approved."))
        self._workflow_write({
            'approved_by_id': self.env.user.id,
            'approved_date': fields.Datetime.now(),
        })
        self._post_to_ledger()

    def action_reject(self):
        self.ensure_one()
        self._check_approver_authorization()
        if self.state != 'submitted':
            raise UserError(_("Only submitted vouchers can be rejected."))
        return {
            'name': _("Reject Voucher"),
            'type': 'ir.actions.act_window',
            'res_model': 'petty.cash.voucher.reason.wizard',
            'view_mode': 'form',
            'target': 'new',
            'context': {
                'default_voucher_id': self.id,
                'default_action_type': 'reject',
            },
        }

    def _action_reject_with_reason(self, reason):
        self.ensure_one()
        self._check_approver_authorization()
        if self.state != 'submitted':
            raise UserError(_("Only submitted vouchers can be rejected."))
        self._workflow_write({
            'rejection_reason': reason,
            'state': 'rejected',
        })
        self.message_post(body=_("Voucher %s was rejected. Reason: %s") % (self.name, reason))

    def action_reset_to_draft(self):
        for voucher in self:
            if voucher.state != 'rejected':
                raise UserError(_("Only rejected vouchers can be reset to draft. Submitted vouchers must be cancelled."))
            is_mgr = self.env.user.has_group('petty_cash_management.group_petty_cash_manager')
            is_creator = (voucher.create_uid == self.env.user or voucher.submitted_by_id == self.env.user)
            is_cust = self.env.user in voucher.fund_id.custodian_ids
            if not (is_mgr or is_creator or is_cust):
                raise UserError(_("Only the creator, an assigned custodian, or a Manager can reset this voucher to draft."))
            voucher._workflow_write({'state': 'draft'})

    def action_cancel(self):
        self.ensure_one()
        if self.state == 'cancelled':
            raise UserError(_("The voucher is already cancelled."))
        if self.state == 'posted':
            # Manager only reversal flow
            if not self.env.user.has_group('petty_cash_management.group_petty_cash_manager'):
                raise UserError(_("Only a Petty Cash Manager can cancel a posted voucher."))
            if self.fund_id.state == 'closed':
                raise UserError(_("Cannot reverse a voucher for a closed petty cash fund."))
        else:
            is_mgr = self.env.user.has_group('petty_cash_management.group_petty_cash_manager')
            is_cust = self.env.user in self.fund_id.custodian_ids
            is_creator = (self.create_uid == self.env.user or self.submitted_by_id == self.env.user)
            if not (is_mgr or is_cust or is_creator):
                raise UserError(_("Only the creator, a custodian, or a Petty Cash Manager can cancel this voucher."))

        return {
            'name': _("Cancel Voucher"),
            'type': 'ir.actions.act_window',
            'res_model': 'petty.cash.voucher.reason.wizard',
            'view_mode': 'form',
            'target': 'new',
            'context': {
                'default_voucher_id': self.id,
                'default_action_type': 'cancel',
            },
        }

    def _action_cancel_with_reason(self, reason):
        self.ensure_one()
        if self.state == 'posted':
            # Manager reversal
            if not self.env.user.has_group('petty_cash_management.group_petty_cash_manager'):
                raise UserError(_("Only a Petty Cash Manager can cancel a posted voucher."))

            if self.fund_id.state == 'closed':
                raise UserError(_("Cannot reverse a voucher for a closed petty cash fund."))

            # Core Odoo _post() requires account.group_account_invoice unless su
            has_invoice_group = self.env.user.has_group('account.group_account_invoice')
            has_account_manager = self.env.user.has_group('account.group_account_manager')
            if not (has_invoice_group or has_account_manager):
                raise UserError(_("Cancelling a posted voucher requires the Invoicing/Billing accounting group (account.group_account_invoice)."))

            # Respect lock dates on reversal
            rev_date = self.date
            violated = self.company_id._get_lock_date_violations(rev_date, fiscalyear=True, hard=True)
            if violated:
                rev_date = fields.Date.context_today(self)

            try:
                # Reversal runs as the real user WITHOUT sudo per specification
                reverse_moves = self.move_id._reverse_moves(
                    default_values_list=[{
                        'date': rev_date,
                        'ref': _("Reversal of %s: %s") % (self.name, reason),
                    }],
                    cancel=True,
                )
            except AccessError as e:
                raise UserError(_("Accounting access error during reversal: %s") % str(e)) from e

            self._workflow_write({
                'reversal_move_id': reverse_moves[:1].id,
                'cancel_reason': reason,
                'state': 'cancelled',
            })
            self.message_post(body=_("Voucher %s was cancelled by reversal entry %s. Reason: %s") % (
                self.name,
                reverse_moves[:1].name or reverse_moves[:1].id,
                reason,
            ))
        else:
            is_mgr = self.env.user.has_group('petty_cash_management.group_petty_cash_manager')
            is_cust = self.env.user in self.fund_id.custodian_ids
            is_creator = (self.create_uid == self.env.user or self.submitted_by_id == self.env.user)
            if not (is_mgr or is_cust or is_creator):
                raise UserError(_("Only the creator, a custodian, or a Petty Cash Manager can cancel this voucher."))

            self._workflow_write({
                'cancel_reason': reason,
                'state': 'cancelled',
            })
            self.message_post(body=_("Voucher %s was cancelled. Reason: %s") % (self.name, reason))

    def _check_approver_authorization(self):
        """Verify user is authorized to approve/reject, checking groups and segregation of duties."""
        is_approver = self.env.user.has_group('petty_cash_management.group_petty_cash_approver')
        is_manager = self.env.user.has_group('petty_cash_management.group_petty_cash_manager')
        if not (is_approver or is_manager):
            raise UserError(_("Only an Approver or Petty Cash Manager can approve or reject vouchers."))

        if not is_manager and self.env.user not in self.fund_id.custodian_ids:
            raise UserError(_("You are not authorized to approve or reject vouchers for this fund."))

        # Segregation of duties: blocked if user is create_uid, submitted_by_id, or employee's user
        blocked_users = {self.create_uid.id, self.submitted_by_id.id}
        if self.employee_id.user_id:
            blocked_users.add(self.employee_id.user_id.id)
        if self.employee_id.work_contact_id and self.employee_id.work_contact_id.user_ids:
            blocked_users.update(self.employee_id.work_contact_id.user_ids.ids)

        if self.env.user.id in blocked_users:
            raise UserError(_("Segregation of duties: You cannot approve or reject a voucher that you created, submitted, or for which you are the payee employee."))

    # -------------------------------------------------------------------------
    # LEDGER POSTING (CRITICAL)
    # -------------------------------------------------------------------------

    def _post_to_ledger(self):
        """Creates and posts the balanced account.move entry in the fund's cash journal.

        Execution and concurrency protection steps:
        1. Row lock: Takes exclusive row-level lock on fund row using fund.lock_for_update() (converts LockError to clear UserError).
        2. Fresh balance read: Invalidates the fund's balance cache and recomputes live balance from posted move lines under the active row lock.
        3. State re-check: Validates voucher state is in ('draft', 'submitted') and no move_id is linked (prevents duplicate posting).
        4. Validates company fiscal lock dates for the voucher accounting date.
        5. Validates available balance >= voucher amount using currency comparison (prevents negative cash).
        6. Creates and posts balanced account.move under scoped sudo() with fund company context.
        """
        self.ensure_one()
        fund = self.fund_id

        # 1. Row lock on fund
        try:
            fund.lock_for_update()
        except LockError:
            raise UserError(_("The fund is being updated by someone else, please try again."))

        # 2. Invalidate cache to read fresh balance
        fund.invalidate_recordset(['balance'])

        # 3. State & duplicate posting guard
        if self.state not in ('draft', 'submitted') or self.move_id:
            raise UserError(_("This voucher has already been posted or is in an invalid state for posting."))

        # 4. Check fiscal lock date on fund company
        # Petty cash funds are cash journals (not sale/purchase), so we pass sale=False, purchase=False,
        # avoiding reading account.journal directly as custodian.
        violated_locks = fund.company_id._get_lock_date_violations(
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

        # 5. Check balance against amount
        if fund.currency_id.compare_amounts(fund.balance, self.amount) < 0:
            raise UserError(_("Insufficient funds: fund '%s' has available balance of %s, but voucher requires %s.") % (
                fund.name,
                formatLang(self.env, fund.balance, currency_obj=fund.currency_id),
                formatLang(self.env, self.amount, currency_obj=self.currency_id),
            ))

        # 6. Resolve ledger lines parameters
        label = self.description
        if self.payee_type == 'partner':
            ledger_partner = self.partner_id
        elif self.payee_type == 'employee':
            ledger_partner = self.employee_id.work_contact_id
        else:  # other
            ledger_partner = False
            label = f"{self.description} - {self.payee_name}"

        analytic_acc = self.analytic_account_id or self.category_id.analytic_account_id or fund.analytic_account_id
        analytic_dist = {str(analytic_acc.id): 100.0} if analytic_acc else False

        # Read default liquidity account from fund (stored computed field with compute_sudo=True)
        # Avoids reading account.journal directly as real user (custodian has no journal read access)
        cash_account_id = fund.default_account_id.id
        if not cash_account_id:
            raise UserError(_("Fund '%s' has no default petty cash account configured.") % fund.name)

        move_vals = {
            'move_type': 'entry',
            'journal_id': fund.journal_id.id,
            'date': self.date,
            'ref': self.name,
            'company_id': fund.company_id.id,
            'line_ids': [
                Command.create({
                    'name': label,
                    'account_id': self.category_id.expense_account_id.id,
                    'debit': self.amount,
                    'credit': 0.0,
                    'partner_id': ledger_partner.id if ledger_partner else False,
                    'analytic_distribution': analytic_dist,
                }),
                Command.create({
                    'name': label,
                    'account_id': cash_account_id,
                    'debit': 0.0,
                    'credit': self.amount,
                    'partner_id': ledger_partner.id if ledger_partner else False,
                }),
            ],
        }

        # The ONE allowed sudo() in Phase 2:
        # Custodians/approvers have no accounting posting rights (account.group_account_invoice).
        # sudo() bypasses the accounting access checks while preserving self.env.uid on create_uid.
        move = self.env['account.move'].with_company(fund.company_id).sudo().create(move_vals)
        move.action_post()

        self._workflow_write({
            'move_id': move.id,
            'state': 'posted',
        })
        self.message_post(body=_("Voucher %s posted to ledger as %s.") % (self.name, move.name or move.id))

    def action_view_journal_entry(self):
        self.ensure_one()
        if not self.move_id:
            raise UserError(_("No journal entry linked to this voucher."))
        return {
            'name': _("Journal Entry"),
            'type': 'ir.actions.act_window',
            'res_model': 'account.move',
            'res_id': self.move_id.id,
            'view_mode': 'form',
        }

    def action_view_reversal_entry(self):
        self.ensure_one()
        if not self.reversal_move_id:
            raise UserError(_("No reversal entry linked to this voucher."))
        return {
            'name': _("Reversal Entry"),
            'type': 'ir.actions.act_window',
            'res_model': 'account.move',
            'res_id': self.reversal_move_id.id,
            'view_mode': 'form',
        }

    def action_view_attachments(self):
        self.ensure_one()
        return {
            'name': _("Attachments"),
            'type': 'ir.actions.act_window',
            'res_model': 'ir.attachment',
            'view_mode': 'list,form',
            'domain': [('res_model', '=', self._name), ('res_id', '=', self.id)],
            'context': {
                'default_res_model': self._name,
                'default_res_id': self.id,
            },
        }


class IrAttachment(models.Model):
    _inherit = 'ir.attachment'

    def unlink(self):
        self._check_petty_cash_voucher_immutability()
        return super().unlink()

    def write(self, vals):
        protected_fields = {'res_id', 'res_model', 'store_fname', 'mimetype', 'raw', 'datas', 'db_datas', 'name'}
        if any(f in vals for f in protected_fields):
            self._check_petty_cash_voucher_immutability()
        return super().write(vals)

    def _check_petty_cash_voucher_immutability(self):
        """Receipt attachments for submitted or posted vouchers cannot be modified or deleted."""
        voucher_attachments = self.filtered(lambda a: a.res_model == 'petty.cash.voucher' and a.res_id)
        if not voucher_attachments:
            return
        vouchers = self.env['petty.cash.voucher'].browse(voucher_attachments.mapped('res_id'))
        locked_vouchers = vouchers.filtered(lambda v: v.state in ('submitted', 'posted'))
        if locked_vouchers:
            raise UserError(_("Receipt attachments cannot be modified or deleted for submitted or posted petty cash vouchers."))
