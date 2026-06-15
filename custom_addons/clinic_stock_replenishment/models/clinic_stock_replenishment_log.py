from odoo import models, fields


class ClinicStockReplenishmentLog(models.Model):
    _name = 'clinic.stock.replenishment.log'
    _description = 'Clinic Stock Replenishment Log'
    _order = 'id desc'

    replenishment_id = fields.Many2one(
        'clinic.stock.replenishment',
        string="Replenishment",
        index=True
    )
    active = fields.Boolean(default=True)

    def unlink(self):
        active_records = self.filtered('active')
        if active_records:
            active_records.write({'active': False})
            return True
        inactive_records = self.filtered(lambda r: not r.active)
        if inactive_records:
            return super(ClinicStockReplenishmentLog, inactive_records).unlink()
        return True

    snapshot_datetime = fields.Datetime(
        string="Snapshot Date & Time",
        readonly=True,
        default=lambda self: fields.Datetime.now(),
    )

    source_warehouse_id = fields.Many2one(
        'stock.warehouse',
        string="Source",
        readonly=True
    )

    destination_warehouse_id = fields.Many2one(
        'stock.warehouse',
        string="Destination Clinic",
        readonly=True,
        index=True
    )

    product_id = fields.Many2one(
        'product.product',
        string="Medicine",
        readonly=True,
        index=True
    )

    # Last 3 days therapy counts
    day_1_count = fields.Integer(string="Day -1", readonly=True)
    day_2_count = fields.Integer(string="Day -2", readonly=True)
    day_3_count = fields.Integer(string="Day -3", readonly=True)

    max_therapy_count = fields.Integer(string="Max Count (Used)", readonly=True)

    # Formula output
    target_qty = fields.Float(string="Target (Formula Output)", readonly=True)

    # Stock at time of snapshot
    current_stock = fields.Float(string="Stock at Snapshot", readonly=True)

    # Final shortage
    shortage_qty = fields.Float(string="Shortage", readonly=True)
    gender_session_count = fields.Char(string="M / F Sessions", readonly=True)