from odoo import models, fields, api
from odoo.exceptions import UserError
from datetime import date, timedelta

class ClinicSelectionWizard(models.TransientModel):
    _name = 'clinic.selection.wizard'
    _description = 'Select Clinic'

    warehouse_id = fields.Many2one('stock.warehouse', string="Select Your Clinic", required=True)

    def action_open_confirmation(self):
        """Show ONLY the latest pending replenishment for this clinic."""
        self.ensure_one()
        Confirmation = self.env['clinic.stock.confirmation']

        # ✅ Use sudo() to bypass ACL on replenishment_id read
        latest = Confirmation.sudo().search([
            ('destination_warehouse_id', '=', self.warehouse_id.id),
            ('active', '=', True),
            ('state', '=', 'pending'),
            ('replenishment_id', '!=', False),
        ], order='create_date desc', limit=1)

        if not latest:
            return {
                'type': 'ir.actions.client',
                'tag': 'display_notification',
                'params': {
                    'title': '✅ All Done!',
                    'message': f'No pending confirmations for {self.warehouse_id.name}. Great job! You can close this window.',
                    'type': 'success',
                    'sticky': False,
                    'next': {'type': 'ir.actions.act_window_close'},
                }
            }

        # ✅ Read via sudo to avoid ACL error
        latest_replenishment_id = latest.replenishment_id.id
        replenishment_name = latest.sudo().replenishment_id.name

        return {
            'type': 'ir.actions.act_window',
            'name': f'Stock Confirmation - {replenishment_name}',
            'res_model': 'clinic.stock.confirmation',
            'view_mode': 'tree,form',
            'domain': [
                ('destination_warehouse_id', '=', self.warehouse_id.id),
                ('replenishment_id', '=', latest_replenishment_id),
                ('active', '=', True),
                ('state', '=', 'pending'),
            ],
            'context': {
                'search_default_destination_warehouse_id': self.warehouse_id.id,
                'create': False,
                'active_clinic_id': self.warehouse_id.id,
                'active_replenishment_id': latest_replenishment_id,
            },
            'target': 'current',
        }

class ClinicTransferSelectionWizard(models.TransientModel):
    _name = 'clinic.transfer.selection.wizard'
    _description = 'Select Clinic and Internal Transfer'

    warehouse_id = fields.Many2one(
        'stock.warehouse',
        string="Select Your Clinic",
        required=True
    )

    picking_id = fields.Many2one(
        'stock.picking',
        string="Internal Transfer Number",
        required=True,
        domain="[('id', 'in', available_picking_ids)]"
    )

    available_picking_ids = fields.Many2many(
        'stock.picking',
        compute='_compute_available_pickings',
        string="Available Transfers"
    )

    @api.depends('warehouse_id')
    def _compute_available_pickings(self):
        from datetime import datetime, timedelta
        for wiz in self:
            if not wiz.warehouse_id:
                wiz.available_picking_ids = False
                continue

            # today start (00:00) and yesterday start (00:00)
            today = fields.Date.context_today(wiz)
            yesterday = today - timedelta(days=1)
            date_from = fields.Datetime.to_datetime(
                fields.Date.to_string(yesterday) + ' 00:00:00'
            )

            # Find confirmations for this clinic, active, from yesterday onwards
            confirmations = self.env['clinic.internal.transfer.confirmation'].search([
                ('destination_warehouse_id', '=', wiz.warehouse_id.id),
                ('active', '=', True),
                ('picking_id', '!=', False),
                ('create_date', '>=', date_from),
            ])

            # Filter out cancelled/done pickings
            valid_pickings = confirmations.mapped('picking_id').filtered(
                lambda p: p.state not in ('cancel', 'done')
            )
            wiz.available_picking_ids = [(6, 0, valid_pickings.ids)]

    @api.onchange('warehouse_id')
    def _onchange_warehouse_id(self):
        """Reset picking when clinic changes"""
        self.picking_id = False

    def action_open_transfer_confirmation(self):
        self.ensure_one()
        return {
            'type': 'ir.actions.act_window',
            'name': f'Confirmation - {self.picking_id.name}',
            'res_model': 'clinic.internal.transfer.confirmation',
            'view_mode': 'tree,form',
            'domain': [
                ('picking_id', '=', self.picking_id.id),
                ('active', '=', True),
            ],
            'context': {
                'create': False,
                # ✅ CRITICAL: Pass picking + clinic to action_confirm_all
                'active_picking_id': self.picking_id.id,
                'active_clinic_id': self.warehouse_id.id,
            },
            'target': 'current',
        }