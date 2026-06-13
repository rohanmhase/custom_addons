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
    difference = fields.Float(related='line_id.difference', readonly=True)

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
        readonly=False
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

    @api.depends('pending_ids', 'difference')
    def _compute_remaining_after_settle(self):
        for w in self:
            w.remaining_after_settle = w.difference + sum(w.pending_ids.mapped('amount'))

    def action_apply(self):
        self.ensure_one()
        line = self.line_id
        reason = self.reason_id
        Pending = self.env['bank.sales.audit.pending']

        if reason.resolution_type == 'write_off':
            # Drop the difference entirely. No pending created.
            line.write({
                'reason_id': reason.id,
                'note': self.note,
                'resolution_state': 'resolved',
                'net_difference': 0.0,
            })

        elif reason.resolution_type == 'carry_forward':
            # Push the entire difference into the pending ledger
            pending = Pending.create({
                'audit_line_id': line.id,
                'bank_config_id': line.bank_config_id.id,
                'tid_number': line.tid_number,
                'clinics_display': line.clinics_display,
                'system_mode': line.system_mode,
                'amount': line.difference,
                'reason_id': reason.id,
                'note': self.note,
                'state': 'open',
            })
            line.write({
                'reason_id': reason.id,
                'note': self.note,
                'resolution_state': 'resolved',
                'net_difference': 0.0,
                'created_pending_id': pending.id,
            })

        elif reason.resolution_type == 'settle_pending':
            selected = self.pending_ids
            net = line.difference + sum(selected.mapped('amount'))

            # Mark selected pending entries as consumed
            selected.write({
                'state': 'settled',
                'settled_by_line_id': line.id,
            })

            # Track the tracking markers on the master audit line
            line.write({
                'reason_id': reason.id,
                'note': self.note,
                'resolution_state': 'resolved',
                'net_difference': net,
            })

            # If settlement didn't fully cancel out, carry the remainder forward
            if abs(net) > 0.0001:
                Pending.create({
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
                line.net_difference = 0.0

        return {'type': 'ir.actions.act_window_close'}