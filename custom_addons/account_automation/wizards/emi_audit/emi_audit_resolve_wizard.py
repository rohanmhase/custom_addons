from odoo import models, fields, api
from markupsafe import Markup


class EmiAuditResolveWizard(models.TransientModel):
    _name = 'emi.audit.resolve.wizard'
    _description = 'Resolve EMI Audit Variance'

    line_id = fields.Many2one(
        'emi.audit.line',
        required=True,
        readonly=True
    )

    # Display context
    clinic_name = fields.Char(
        related='line_id.clinic_name', readonly=True
    )
    odoo_emi_sales = fields.Float(
        related='line_id.odoo_emi_sales', readonly=True
    )
    expected_odoo_amount = fields.Float(
        related='line_id.expected_odoo_amount', readonly=True
    )
    difference = fields.Float(
        related='line_id.difference', readonly=True
    )

    amount_to_resolve = fields.Float(
        string='Amount To Resolve',
        compute='_compute_amount_to_resolve',
        readonly=True
    )

    @api.depends(
        'line_id.resolution_state',
        'line_id.difference',
        'line_id.net_difference'
    )
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
        'emi.audit.reason',
        string='Reason',
        required=True
    )
    resolution_type = fields.Selection(
        related='reason_id.resolution_type',
        readonly=True
    )
    note = fields.Char(string='Note')

    pending_ids = fields.Many2many(
        'emi.audit.pending',
        string='Pending Variances',
        compute='_compute_pending_ids',
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
                ('provider_id', '=', wizard.line_id.provider_id.id),
                ('state', '=', 'open'),
            ]

            if wizard.line_id.clinic_id:
                domain.append(
                    ('clinic_id', '=', wizard.line_id.clinic_id.id)
                )

            matching = self.env['emi.audit.pending'].search(domain)
            wizard.pending_ids = [(6, 0, matching.ids)]

    remaining_after_settle = fields.Float(
        string='Remaining After Settlement',
        compute='_compute_remaining_after_settle'
    )

    @api.depends('pending_ids', 'amount_to_resolve')
    def _compute_remaining_after_settle(self):
        for w in self:
            w.remaining_after_settle = (
                w.amount_to_resolve + sum(w.pending_ids.mapped('amount'))
            )

    def action_apply(self):
        self.ensure_one()
        line = self.line_id
        reason = self.reason_id
        Pending = self.env['emi.audit.pending']

        amount = (
            line.net_difference
            if line.resolution_state == 'resolved'
            else line.difference
        )

        # Clean up old pending if re-resolving
        if line.created_pending_id:
            old = line.created_pending_id
            line.write({'created_pending_id': False})
            if old.state == 'open':
                old.unlink()

        if reason.resolution_type == 'write_off':
            line.write({
                'reason_id': reason.id,
                'note': self.note,
                'resolution_state': 'resolved',
                'net_difference': 0.0,
            })

        elif reason.resolution_type == 'carry_forward':
            pending = Pending.create({
                'audit_line_id': line.id,
                'provider_id': line.provider_id.id,
                'clinic_id': line.clinic_id.id if line.clinic_id else False,
                'clinic_name': line.clinic_name,
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

            selected.write({
                'state': 'settled',
                'settled_by_line_id': line.id,
            })

            line_vals = {
                'reason_id': reason.id,
                'note': self.note,
                'resolution_state': 'resolved',
                'net_difference': net,
            }

            if abs(net) > 0.0001:
                remainder = Pending.create({
                    'audit_line_id': line.id,
                    'provider_id': line.provider_id.id,
                    'clinic_id': (
                        line.clinic_id.id if line.clinic_id else False
                    ),
                    'clinic_name': line.clinic_name,
                    'amount': net,
                    'reason_id': reason.id,
                    'note': f"Remainder after settling {self.note or ''}".strip(),
                    'state': 'open',
                })
                line_vals['created_pending_id'] = remainder.id

            line.write(line_vals)

        # Log the resolution to the main audit chatter
        if line.audit_id:
            identifier = (
                f"Invoice <code>{line.invoice_no}</code>"
                if line.invoice_no
                else f"Clinic <code>{line.clinic_name or 'N/A'}</code>"
            )
            line.audit_id.message_post(
                body=Markup(
                    f"<strong>Line Resolved:</strong> "
                    f"{identifier} was resolved using reason: <em>{reason.name}</em>."
                ),
                subtype_xmlid="mail.mt_note"
            )

        return {'type': 'ir.actions.act_window_close'}