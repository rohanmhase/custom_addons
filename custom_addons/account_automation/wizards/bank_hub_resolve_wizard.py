from odoo import models, fields, api


class BankSalesAuditResolveWizard(models.TransientModel):
    _name = 'bank.sales.audit.resolve.wizard'
    _description = 'Resolve Bank vs HUB Variance'

    line_id = fields.Many2one(
        'bank.sales.audit.line',
        required=True,
        readonly=True
    )

    # Display-only context fields
    tid_number = fields.Char(related='line_id.tid_number', readonly=True)
    clinics_display = fields.Char(related='line_id.clinics_display', readonly=True)
    system_mode = fields.Char(related='line_id.system_mode', readonly=True)
    hub_amount = fields.Float(related='line_id.hub_amount', readonly=True)
    bank_amount = fields.Float(related='line_id.bank_amount', readonly=True)

    # Original variance from THIS audit run — shown for context only.
    # Does NOT drive the calculation on a 2nd+ pass (see amount_to_resolve).
    difference = fields.Float(related='line_id.difference', readonly=True)

    # THE amount this wizard actually acts on.
    #   1st pass (line not yet resolved) -> line.difference
    #   2nd+ pass (line already resolved) -> line.net_difference (leftover)
    amount_to_resolve = fields.Float(
        string='Amount To Resolve',
        compute='_compute_amount_to_resolve',
        readonly=True
    )

    @api.depends('line_id.resolution_state', 'line_id.difference', 'line_id.net_difference')
    def _compute_amount_to_resolve(self):
        for w in self:
            line = w.line_id
            if not line:
                w.amount_to_resolve = 0.0
            elif line.resolution_state == 'resolved':
                w.amount_to_resolve = line.net_difference
            else:
                w.amount_to_resolve = line.difference

    reason_id = fields.Many2one(
        'bank.sales.audit.reason',
        string='Reason',
        required=True
    )

    resolution_type = fields.Selection(
        related='reason_id.resolution_type',
        readonly=True
    )

    note = fields.Char(string='Note')

    pending_ids = fields.Many2many(
        'bank.sales.audit.pending',
        string="Pending Variances",
        compute="_compute_pending_ids",
        store=True,
        readonly=False,
        domain=[('state', '=', 'open')]
    )

    @api.depends('line_id')
    def _compute_pending_ids(self):
        for wizard in self:
            if not wizard.line_id:
                wizard.pending_ids = [(5, 0, 0)]
                continue

            domain = [
                ('bank_config_id', '=', wizard.line_id.bank_config_id.id),
                ('state', '=', 'open')
            ]

            if wizard.line_id.tid_number:
                domain.append(('tid_number', '=', wizard.line_id.tid_number.strip()))
            else:
                domain.append(('tid_number', '=', False))

            if wizard.line_id.system_mode:
                domain.append(('system_mode', '=ilike', wizard.line_id.system_mode.strip()))
            else:
                domain.append(('system_mode', '=', False))

            matching_records = self.env['bank.sales.audit.pending'].search(domain)
            wizard.pending_ids = [(6, 0, matching_records.ids)]

    # Preview of remaining variance after settlement (computed, not stored)
    remaining_after_settle = fields.Float(
        string='Remaining After Settlement',
        compute='_compute_remaining_after_settle'
    )

    @api.depends('pending_ids', 'amount_to_resolve')
    def _compute_remaining_after_settle(self):
        for w in self:
            w.remaining_after_settle = w.amount_to_resolve + sum(w.pending_ids.mapped('amount'))

    def action_apply(self):
        self.ensure_one()
        line = self.line_id
        reason = self.reason_id
        Pending = self.env['bank.sales.audit.pending']

        # Amount this resolution pass is acting on. On a 2nd+ pass this
        # is the leftover (net_difference) from the previous pass.
        amount = line.net_difference if line.resolution_state == 'resolved' else line.difference

        # If this line is being resolved AGAIN (2nd+ pass), it may already
        # have an open pending entry from the previous pass — that entry
        # represented the SAME leftover we're now acting on, so clear it
        # out first to avoid leaving an orphaned duplicate in the ledger.
        if line.created_pending_id:
            old_pending = line.created_pending_id
            line.write({'created_pending_id': False})
            if old_pending.state == 'open':
                old_pending.unlink()
            # if it was already 'settled' by a later line, leave it —
            # that history is real and shouldn't be touched here.

        if reason.resolution_type == 'write_off':
            # Drop the amount entirely. Nothing remains pending.
            line.write({
                'reason_id': reason.id,
                'note': self.note,
                'resolution_state': 'resolved',
                'net_difference': 0.0,
            })

        elif reason.resolution_type == 'carry_forward':
            # The ENTIRE amount moves into the pending ledger.
            # net_difference = amount (fully outstanding, tracked via
            # created_pending_id).
            pending = Pending.create({
                'audit_line_id': line.id,
                'bank_config_id': line.bank_config_id.id,
                'tid_number': line.tid_number,
                'clinics_display': line.clinics_display,
                'system_mode': line.system_mode,
                'amount': amount,
                'reason_id': reason.id,
                'note': self.note,
                'state': 'open',
            })
            line.write({
                'reason_id': reason.id,
                'note': self.note,
                'resolution_state': 'resolved',
                'net_difference': amount,
                'created_pending_id': pending.id,
            })

        elif reason.resolution_type == 'settle_pending':
            selected = self.pending_ids
            net = amount + sum(selected.mapped('amount'))

            # Mark selected pending entries as consumed by this line
            selected.write({
                'state': 'settled',
                'settled_by_line_id': line.id,
            })

            line_vals = {
                'reason_id': reason.id,
                'note': self.note,
                'resolution_state': 'resolved',
                # net_difference shows whatever is STILL left over.
                # 0 = fully cleared. Non-zero = still outstanding,
                # tracked below via a new pending entry.
                'net_difference': net,
            }

            # If settlement didn't fully cancel out, push the remainder
            # into a new pending entry AND link it back to this line so
            # the history view / 2nd Resolve pass can pick it up.
            if abs(net) > 0.0001:
                remainder_pending = Pending.create({
                    'audit_line_id': line.id,
                    'bank_config_id': line.bank_config_id.id,
                    'tid_number': line.tid_number,
                    'clinics_display': line.clinics_display,
                    'system_mode': line.system_mode,
                    'amount': net,
                    'reason_id': reason.id,
                    'note': f"Remainder after settling {self.note or ''}".strip(),
                    'state': 'open',
                })
                line_vals['created_pending_id'] = remainder_pending.id

            line.write(line_vals)

        return {'type': 'ir.actions.act_window_close'}