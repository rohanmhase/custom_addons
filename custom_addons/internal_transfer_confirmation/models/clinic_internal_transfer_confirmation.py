# clinic_internal_transfer_confirmation.py
from odoo import models, fields, api
from datetime import date


class ClinicInternalTransferConfirmation(models.Model):
    _name = 'clinic.internal.transfer.confirmation'
    _description = 'Clinic Internal Transfer Confirmation'
    _inherit = ['mail.thread', 'mail.activity.mixin']
    _order = 'create_date desc, picking_id, product_id'

    picking_id = fields.Many2one(
        'stock.picking',
        string="Internal Transfer",
        readonly=True,
        ondelete='set null',
        index=True,
    )
    move_line_id = fields.Many2one(
        'stock.move',
        string="Stock Move",
        readonly=True,
        ondelete='set null',
    )
    transfer_name = fields.Char(
        string="Transfer Name",
        related='picking_id.name',
        store=True,
        readonly=True,
    )
    transfer_date = fields.Datetime(
        string="Date Created",
        related='picking_id.create_date',
        store=True,
        readonly=True,
        index=True,
    )
    destination_warehouse_id = fields.Many2one(
        'stock.warehouse',
        string="Clinic",
        readonly=True,
        index=True,
    )
    product_id = fields.Many2one(
        'product.product',
        string="Medicine",
        readonly=True,
        index=True,
    )

    sent_quantity = fields.Integer(string="Quantity Sent", readonly=True)
    received_quantity = fields.Integer(
        string="Actual Received Quantity",
        readonly=False,
    )
    difference_qty = fields.Integer(
        string="Difference",
        compute='_compute_difference',
        store=True,
    )

    state = fields.Selection([
        ('pending', 'Pending'),
        ('confirmed', 'Confirmed'),
    ], default='pending', string="Status")

    confirmed_by = fields.Many2one('res.users', string="Confirmed By", readonly=True)
    confirmation_date = fields.Datetime(string="Confirmed On", readonly=True)
    active = fields.Boolean(default=True)
    problem_raised = fields.Boolean(string="Problem Raised", default=False)
    problem_notes = fields.Text(
        string="Notes",
        readonly=False,
    )

    # ─────────────────────────────────────────────
    # RESOLUTION TRACKING (Warehouse side)
    # ─────────────────────────────────────────────
    resolution_status = fields.Selection([
        ('unresolved', 'Unresolved'),
        ('resolved', 'Resolved'),
        ('skipped', 'Skipped'),
    ], string="Resolution Status", index=True)

    resolution_notes = fields.Text(string="Resolution Notes")
    resolved_by = fields.Many2one('res.users', string="Resolved By", readonly=True)
    resolved_date = fields.Datetime(string="Resolved On", readonly=True)

    is_problem = fields.Boolean(
        string="Is Problem",
        compute='_compute_is_problem',
        store=True,
        index=True,
    )

    @api.depends('sent_quantity', 'received_quantity')
    def _compute_difference(self):
        for rec in self:
            rec.difference_qty = rec.received_quantity - rec.sent_quantity

    @api.depends('problem_raised', 'difference_qty', 'problem_notes')
    def _compute_is_problem(self):
        for rec in self:
            rec.is_problem = bool(
                rec.problem_raised
                or (rec.difference_qty and rec.difference_qty != 0)
                or (rec.problem_notes and rec.problem_notes.strip())
            )

    def action_raise_problem(self):
        """Open wizard to raise problem for internal transfer"""
        self.ensure_one()
        wizard = self.env['clinic.transfer.problem.wizard'].create({
            'confirmation_id': self.id,
            'transfer_name': self.transfer_name,
            'destination_warehouse_id': self.destination_warehouse_id.id,
            'product_id': self.product_id.id,
            'sent_quantity': self.sent_quantity,
            'received_quantity': self.received_quantity or 0.0,
            'problem_notes': self.problem_notes or '',
        })
        return {
            'type': 'ir.actions.act_window',
            'name': 'Raise Problem',
            'res_model': 'clinic.transfer.problem.wizard',
            'res_id': wizard.id,
            'view_mode': 'form',
            'target': 'new',
            'context': {
                'form_view_initial_mode': 'edit',
                'create': False,
                'edit': True,
            },
        }

    def action_confirm_all(self):
        """Confirm all pending lines for CURRENT picking + clinic ONLY."""
        from odoo.exceptions import UserError

        # Get the picking and clinic from context (set by wizard)
        picking_id = self.env.context.get('active_picking_id')
        clinic_id = self.env.context.get('active_clinic_id')

        domain = [
            ('state', '=', 'pending'),
            ('problem_raised', '=', False),
            ('active', '=', True),
        ]
        # ✅ CRITICAL: Only confirm for the specific picking + clini c
        if picking_id:
            domain.append(('picking_id', '=', picking_id))
        if clinic_id:
            domain.append(('destination_warehouse_id', '=', clinic_id))

        # Fallback: use active_ids from selection
        if not picking_id and not clinic_id:
            active_ids = self.env.context.get('active_ids', [])
            if active_ids:
                domain.append(('id', 'in', active_ids))
            else:
                raise UserError(
                    "Cannot determine which transfer to confirm. "
                    "Please open through the Internal Transfer Confirmation wizard."
                )

        records = self.search(domain)
        for rec in records:
            rec.received_quantity = rec.sent_quantity
        if records:
            records.write({
                'state': 'confirmed',
                'confirmed_by': self.env.user.id,
                'confirmation_date': fields.Datetime.now()
            })
        return {'type': 'ir.actions.client', 'tag': 'reload'}

    # ─────────────────────────────────────────────
    # ✅ WAREHOUSE RESOLUTION ACTIONS (via WIZARD)
    # ─────────────────────────────────────────────
    def action_mark_resolved(self):
        """Open wizard to enter resolution note before marking resolved."""
        self.ensure_one()
        wizard = self.env['clinic.resolve.transfer.problem.wizard'].create({
            'confirmation_id': self.id,
            'resolution_notes': self.resolution_notes or '',
        })
        return {
            'type': 'ir.actions.act_window',
            'name': 'Resolve Transfer Problem',
            'res_model': 'clinic.resolve.transfer.problem.wizard',
            'res_id': wizard.id,
            'view_mode': 'form',
            'target': 'new',
            'context': {
                'form_view_initial_mode': 'edit',
                'create': False,
                'edit': True,
            },
        }

    def action_skip_problem(self):
        """Warehouse skips this transfer problem."""
        for rec in self:
            rec.write({
                'resolution_status': 'skipped',
                'resolved_by': self.env.user.id,
                'resolved_date': fields.Datetime.now(),
            })
        return True

    def action_reopen_problem(self):
        """Undo resolved/skipped — set back to unresolved."""
        for rec in self:
            rec.write({
                'resolution_status': 'unresolved',
                'resolved_by': False,
                'resolved_date': False,
                'resolution_notes': False,
            })
        return True

    def unlink(self):
        self.write({'active': False})
        return True


class ClinicTransferProblemWizard(models.TransientModel):
    _name = 'clinic.transfer.problem.wizard'
    _description = 'Raise Problem Wizard for Transfer Confirmation'

    confirmation_id = fields.Many2one('clinic.internal.transfer.confirmation', required=True)
    transfer_name = fields.Char(string="Internal Transfer", readonly=True)
    destination_warehouse_id = fields.Many2one('stock.warehouse', string="Clinic", readonly=True)
    product_id = fields.Many2one('product.product', string="Medicine", readonly=True)
    sent_quantity = fields.Integer(string="Sent Quantity", readonly=True)
    received_quantity = fields.Integer(string="Actual Received Quantity")
    problem_notes = fields.Text(string="Notes")

    def action_save_and_confirm(self):
        """Save data back to confirmation record and mark confirmed"""
        self.ensure_one()
        self.confirmation_id.write({
            'received_quantity': self.received_quantity,
            'problem_notes': self.problem_notes,
            'problem_raised': True,
            'state': 'confirmed',
            'confirmed_by': self.env.user.id,
            'confirmation_date': fields.Datetime.now(),
            'resolution_status': 'unresolved',  # ✅ Only set when problem raised
        })
        return {'type': 'ir.actions.act_window_close'}


# ═══════════════════════════════════════════════════════════
# ✅ NEW: WAREHOUSE RESOLVE PROBLEM WIZARD (TRANSFER)
# ═══════════════════════════════════════════════════════════
class ClinicResolveTransferProblemWizard(models.TransientModel):
    _name = 'clinic.resolve.transfer.problem.wizard'
    _description = 'Warehouse Resolve Transfer Problem Wizard'

    confirmation_id = fields.Many2one(
        'clinic.internal.transfer.confirmation', required=True, readonly=True
    )
    product_name = fields.Char(
        related='confirmation_id.product_id.name', readonly=True, string="Medicine"
    )
    clinic_name = fields.Char(
        related='confirmation_id.destination_warehouse_id.name', readonly=True, string="Clinic"
    )
    transfer_name = fields.Char(
        related='confirmation_id.transfer_name', readonly=True, string="Transfer"
    )
    clinic_note = fields.Text(
        related='confirmation_id.problem_notes', readonly=True, string="Clinic's Note"
    )
    sent_quantity = fields.Integer(
        related='confirmation_id.sent_quantity', readonly=True
    )
    received_quantity = fields.Integer(
        related='confirmation_id.received_quantity', readonly=True
    )
    difference_qty = fields.Integer(
        related='confirmation_id.difference_qty', readonly=True
    )
    resolution_notes = fields.Text(string="Resolution Note")

    def action_confirm_resolve(self):
        self.ensure_one()
        self.confirmation_id.write({
            'resolution_status': 'resolved',
            'resolution_notes': self.resolution_notes,
            'resolved_by': self.env.user.id,
            'resolved_date': fields.Datetime.now(),
        })
        return {'type': 'ir.actions.act_window_close'}


class StockPickingInherit(models.Model):
    _inherit = 'stock.picking'

    def _create_clinic_transfer_confirmations(self):
        import logging
        _logger = logging.getLogger(__name__)

        Confirmation = self.env['clinic.internal.transfer.confirmation']
        Warehouse = self.env['stock.warehouse']

        for picking in self:
            _logger.info(f"===== CLINIC DEBUG: {picking.name} state={picking.state} =====")

            if not picking.picking_type_id or picking.picking_type_id.code != 'internal':
                continue
            if picking.state != 'assigned':
                continue

            existing = Confirmation.search([
                ('picking_id', '=', picking.id),
                ('active', '=', True)
            ], limit=1)
            if existing:
                _logger.info(f"SKIP: Already exists")
                continue

            dest_location = picking.location_dest_id
            dest_warehouse = False
            current_loc = dest_location
            while current_loc and not dest_warehouse:
                dest_warehouse = Warehouse.search([
                    ('lot_stock_id', '=', current_loc.id)
                ], limit=1)
                if not dest_warehouse:
                    dest_warehouse = Warehouse.search([
                        ('view_location_id', '=', current_loc.id)
                    ], limit=1)
                if dest_warehouse:
                    break
                current_loc = current_loc.location_id

            _logger.info(f"Warehouse: {dest_warehouse.name if dest_warehouse else 'NONE'}")

            vals_list = []
            for move in picking.move_ids:
                if not move.product_id:
                    continue
                vals_list.append({
                    'picking_id': picking.id,
                    'move_line_id': move.id,
                    'destination_warehouse_id': dest_warehouse.id if dest_warehouse else False,
                    'product_id': move.product_id.id,
                    'sent_quantity': int(move.product_uom_qty),
                    'state': 'pending',
                })

            if vals_list:
                created = Confirmation.create(vals_list)
                _logger.info(f"CREATED {len(created)} lines")

    def _compute_state(self):
        result = super()._compute_state()
        assigned_pickings = self.filtered(
            lambda p: p.state == 'assigned'
                      and p.picking_type_id
                      and p.picking_type_id.code == 'internal'
        )
        if assigned_pickings:
            assigned_pickings._create_clinic_transfer_confirmations()
        return result

    def action_assign(self):
        result = super().action_assign()
        self._create_clinic_transfer_confirmations()
        return result

    def write(self, vals):
        result = super().write(vals)
        if vals.get('state') == 'assigned':
            self._create_clinic_transfer_confirmations()
        return result

    def unlink(self):
        confirmations = self.env['clinic.internal.transfer.confirmation'].search([
            ('picking_id', 'in', self.ids),
            ('active', '=', True)
        ])
        if confirmations:
            confirmations.write({'active': False})
        return super().unlink()
