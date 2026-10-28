from odoo import models, fields, Command, _
from odoo.exceptions import UserError


class MergeOldInvoicesWizard(models.TransientModel):
    _name = 'merge.old.invoices.wizard'
    _description = 'Merge Old Tele-consultation Invoices'

    date_from = fields.Date(required=True)
    date_to = fields.Date(required=True)
    state = fields.Selection([
        ('draft', 'Draft'),
        ('done', 'Done'),
    ], default='draft')
    summary = fields.Text(readonly=True)

    def _get_candidates(self):
        """Tele-Medicine customers only (partner nature = online_sales), not already migrated."""
        done_ids = self.env['teleconsult.invoice.line'].sudo().search([]).mapped('move_id').ids
        return self.env['account.move'].search([
            ('move_type', '=', 'out_invoice'),
            ('partner_id.nature_id.code', '=', 'online_sales'),
            ('invoice_date', '>=', self.date_from),
            ('invoice_date', '<=', self.date_to),
            ('id', 'not in', done_ids),
        ])

    def action_preview(self):
        self.ensure_one()
        moves = self._get_candidates()
        self.summary = _(
            "Candidates: %s invoices\n"
            "(partner nature = online_sales, not yet migrated, in date range)"
        ) % len(moves)
        return self._reload()

    def action_merge(self):
        self.ensure_one()
        config = self.env['online.import.config'].search([
            ('company_id', '=', self.env.company.id)
        ], limit=1) or self.env['online.import.config'].search([], limit=1)

        if not config or not config.merged_product_id:
            raise UserError(_("Set Combined Product (Pre-Split) in Online Imports Configuration first."))

        moves = self._get_candidates()
        if not moves:
            raise UserError(_("No candidate invoices found."))

        TeleLine = self.env['teleconsult.invoice.line']
        ok, errors = 0, []

        for move in moves:
            # Pre-fetch label into Python memory before entering savepoint
            move_label = move.name or move.ref or str(move.id)
            try:
                with self.env.cr.savepoint():
                    self._merge_one(move, config, TeleLine)
                ok += 1
            except Exception as e:
                self.env.invalidate_all()
                errors.append("%s: %s" % (move_label, str(e)))

        self.state = 'done'
        self.summary = _("Merged: %s\nFailed: %s\n%s") % (
            ok, len(errors), '\n'.join(errors[:30])
        )
        return self._reload()

    def _merge_one(self, move, config, TeleLine):
        was_posted = move.state == 'posted'
        pay_moves = self.env['account.move']

        if was_posted:
            recv = move.line_ids.filtered(
                lambda l: l.account_id.account_type == 'asset_receivable'
            )
            for aml in recv:
                for m in aml.matched_debit_ids:
                    pay_moves |= m.debit_move_id.move_id
                for m in aml.matched_credit_ids:
                    pay_moves |= m.credit_move_id.move_id
            partials = recv.matched_debit_ids | recv.matched_credit_ids
            if partials:
                partials.unlink()
            move.button_draft()

        product_lines = move.invoice_line_ids.filtered(lambda l: l.display_type == 'product')
        if not product_lines:
            raise UserError(_("No product lines"))

        total_untaxed = 0.0
        total_incl = 0.0
        tax_ids = []
        tax_pct = 0.0
        tele_vals = []

        for pl in product_lines:
            sub_base = pl.quantity * pl.price_unit
            if pl.discount:
                sub_base *= (1.0 - pl.discount / 100.0)

            line_tax_pct = 0.0
            if pl.tax_ids:
                tax_ids = pl.tax_ids.ids
                line_tax_pct = sum(t.amount for t in pl.tax_ids if t.amount_type == 'percent')
                tax_pct = line_tax_pct

            sub_incl = sub_base * (1.0 + line_tax_pct / 100.0)
            total_untaxed += sub_base
            total_incl += sub_incl

            label = pl.name or (pl.product_id.display_name if pl.product_id else 'Item')
            tele_vals.append({
                'move_id': move.id,
                'product_name': label,
                'quantity': pl.quantity,
            })

        split_date = config.split_start_date or fields.Date.from_string('2026-11-01')
        inv_date = move.invoice_date or fields.Date.context_today(self)
        lines_cmd = []

        if inv_date >= split_date and config.consultation_product_id:
            consult_product = config.consultation_product_id
            consult_std = config.consultation_amount or 600.0
            min_med_incl = config.min_medicine_amount or 50.0

            if total_incl >= (consult_std + min_med_incl):
                consult_price = consult_std
                consult_disc = 0.0
                med_target_incl = total_incl - consult_std
            else:
                med_target_incl = min_med_incl
                consult_target_net = max(total_incl - min_med_incl, 0.0)
                consult_price = consult_std
                consult_disc = ((consult_std - consult_target_net) / consult_std) * 100.0 if consult_std else 0.0

            med_base_price = med_target_incl / (1.0 + tax_pct / 100.0) if (1.0 + tax_pct / 100.0) else med_target_incl

            lines_cmd.append(Command.create({
                'product_id': consult_product.id,
                'quantity': 1.0,
                'price_unit': round(consult_price, 2),
                'discount': round(consult_disc, 2),
                'tax_ids': [],
            }))
            if not config.medicine_product_id:
                raise UserError(_("Set Medicine Product in Online Imports Configuration first."))
            lines_cmd.append(Command.create({
                'product_id': config.medicine_product_id.id,
                'quantity': 1.0,
                'price_unit': round(med_base_price, 2),
                'discount': 0.0,
                'tax_ids': [Command.set(tax_ids)] if tax_ids else [],
            }))
        else:
            lines_cmd.append(Command.create({
                'product_id': config.merged_product_id.id,
                'quantity': 1.0,
                'price_unit': round(total_untaxed, 2),
                'discount': 0.0,
                'tax_ids': [Command.set(tax_ids)] if tax_ids else [],
            }))

        product_lines.unlink()
        move.write({
            'narration': False,
            'invoice_line_ids': lines_cmd,
        })

        if was_posted:
            move.action_post()
            if pay_moves:
                inv_recv = move.line_ids.filtered(
                    lambda l: l.account_id.account_type == 'asset_receivable' and not l.reconciled
                )
                pay_recv = pay_moves.mapped('line_ids').filtered(
                    lambda l: l.account_id.account_type == 'asset_receivable' and not l.reconciled
                )
                if inv_recv and pay_recv:
                    (inv_recv | pay_recv).reconcile()

        TeleLine.create(tele_vals)

    def _reload(self):
        return {
            'type': 'ir.actions.act_window',
            'res_model': self._name,
            'res_id': self.id,
            'view_mode': 'form',
            'target': 'new',
        }