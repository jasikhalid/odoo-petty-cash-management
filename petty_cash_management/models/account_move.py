
from odoo import models, _
from odoo.tools.misc import formatLang


class AccountMove(models.Model):
    _inherit = 'account.move'

    def button_draft(self):
        res = super().button_draft()
        self._sync_petty_cash('reset_or_cancel')
        return res

    def button_cancel(self):
        res = super().button_cancel()
        self._sync_petty_cash('reset_or_cancel')
        return res

    def _post(self, soft=True):
        posted = super()._post(soft=soft)
        posted._sync_petty_cash('post')
        return posted

    def _sync_petty_cash(self, action):
        """Keep linked petty cash vouchers and top-ups in sync with journal entries.

        Uses ONE scoped sudo() call so accountants without petty cash groups can
        synchronize status when journal entries are reset to draft, cancelled,
        reversed, or re-posted.
        """
        if not self:
            return

        # Optimization: only moves in cash journals can ever be linked to petty cash
        cash_moves = self.filtered(lambda m: m.journal_id.type == 'cash')
        if not cash_moves:
            return

        su_env = self.sudo().env

        if action == 'reset_or_cancel':
            vouchers = su_env['petty.cash.voucher'].search([
                ('move_id', 'in', cash_moves.ids),
                ('state', '=', 'posted'),
            ])
            for voucher in vouchers:
                voucher._workflow_write({'state': 'cancelled'})
                voucher.message_post(
                    body=_("Journal entry %(entry)s was reset/cancelled by %(user)s.",
                           entry=voucher.move_id.display_name or voucher.move_id.name,
                           user=self.env.user.display_name),
                    author_id=self.env.user.partner_id.id,
                )

            topups = su_env['petty.cash.topup'].search([
                ('move_id', 'in', cash_moves.ids),
                ('state', '=', 'posted'),
            ])
            for topup in topups:
                topup._workflow_write({'state': 'cancelled'})
                topup.message_post(
                    body=_("Journal entry %(entry)s was reset/cancelled by %(user)s.",
                           entry=topup.move_id.display_name or topup.move_id.name,
                           user=self.env.user.display_name),
                    author_id=self.env.user.partner_id.id,
                )

        elif action == 'post':
            # Case A: Standard reversal move(s) posted -> cancel original voucher/top-up
            rev_moves = cash_moves.filtered(lambda m: m.reversed_entry_id)
            if rev_moves:
                origin_moves = rev_moves.reversed_entry_id
                rev_by_origin = {m.reversed_entry_id.id: m for m in rev_moves}

                vouchers = su_env['petty.cash.voucher'].search([
                    ('move_id', 'in', origin_moves.ids),
                    ('state', '=', 'posted'),
                ])
                for voucher in vouchers:
                    rev_move = rev_by_origin.get(voucher.move_id.id)
                    vals = {'state': 'cancelled'}
                    if 'reversal_move_id' in voucher._fields and not voucher.reversal_move_id and rev_move:
                        vals['reversal_move_id'] = rev_move.id
                    voucher._workflow_write(vals)
                    voucher.message_post(
                        body=_("Journal entry %(entry)s was reversed by reversal entry %(reversal)s by %(user)s.",
                               entry=voucher.move_id.display_name or voucher.move_id.name,
                               reversal=rev_move.display_name or rev_move.name if rev_move else '',
                               user=self.env.user.display_name),
                        author_id=self.env.user.partner_id.id,
                    )

                topups = su_env['petty.cash.topup'].search([
                    ('move_id', 'in', origin_moves.ids),
                    ('state', '=', 'posted'),
                ])
                for topup in topups:
                    rev_move = rev_by_origin.get(topup.move_id.id)
                    vals = {'state': 'cancelled'}
                    if 'reversal_move_id' in topup._fields and not topup.reversal_move_id and rev_move:
                        vals['reversal_move_id'] = rev_move.id
                    topup._workflow_write(vals)
                    topup.message_post(
                        body=_("Journal entry %(entry)s was reversed by reversal entry %(reversal)s by %(user)s.",
                               entry=topup.move_id.display_name or topup.move_id.name,
                               reversal=rev_move.display_name or rev_move.name if rev_move else '',
                               user=self.env.user.display_name),
                        author_id=self.env.user.partner_id.id,
                    )

            # Case B: Re-posting move of cancelled voucher / top-up
            vouchers = su_env['petty.cash.voucher'].search([
                ('move_id', 'in', cash_moves.ids),
                ('state', '=', 'cancelled'),
            ])
            for voucher in vouchers:
                move = voucher.move_id
                has_posted_reversal = bool(move.reversal_move_ids.filtered(lambda m: m.state == 'posted'))
                has_reversal = bool(
                    (voucher.reversal_move_id and voucher.reversal_move_id.state == 'posted')
                    or has_posted_reversal
                )
                if not has_reversal:
                    voucher._workflow_write({'state': 'posted'})
                    voucher.message_post(
                        body=_("Journal entry %(entry)s was re-posted by %(user)s. Expense restored to Posted.",
                               entry=move.display_name or move.name,
                               user=self.env.user.display_name),
                        author_id=self.env.user.partner_id.id,
                    )
                    fund = voucher.fund_id
                    fund.invalidate_recordset(['balance'])
                    if fund.currency_id.compare_amounts(fund.balance, 0.0) < 0:
                        voucher.message_post(
                            body=_("Warning: Re-posting journal entry %(entry)s caused fund '%(fund)s' balance to become negative (%(balance)s).",
                                   entry=move.display_name or move.name,
                                   fund=fund.name,
                                   balance=formatLang(self.env, fund.balance, currency_obj=fund.currency_id)),
                            author_id=self.env.user.partner_id.id,
                        )

            topups = su_env['petty.cash.topup'].search([
                ('move_id', 'in', cash_moves.ids),
                ('state', '=', 'cancelled'),
            ])
            for topup in topups:
                move = topup.move_id
                has_posted_reversal = bool(move.reversal_move_ids.filtered(lambda m: m.state == 'posted'))
                if not has_posted_reversal:
                    topup._workflow_write({'state': 'posted'})
                    topup.message_post(
                        body=_("Journal entry %(entry)s was re-posted by %(user)s. Top-up restored to Posted.",
                               entry=move.display_name or move.name,
                               user=self.env.user.display_name),
                        author_id=self.env.user.partner_id.id,
                    )
                    topup.fund_id.invalidate_recordset(['balance'])
