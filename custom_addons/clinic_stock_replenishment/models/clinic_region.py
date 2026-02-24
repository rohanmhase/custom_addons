from odoo import models, fields


class ClinicStockRegion(models.Model):
    _name = 'clinic.stock.region'
    _description = 'Clinic Stock Region'
    _inherit = ['mail.thread', 'mail.activity.mixin']

    name = fields.Char(required=True, tracking=True)

    active = fields.Boolean(default=True, tracking=True)

    warehouse_ids = fields.Many2many(
        'stock.warehouse',
        string="Clinics",
        tracking=True
    )

    def unlink(self):
        # If record is active, archive it instead (send to recycle bin)
        active_records = self.filtered('active')
        if active_records:
            active_records.write({'active': False})

        # If record is already archived, hard delete it
        inactive_records = self.filtered(lambda r: not r.active)
        if inactive_records:
            return super(StockCountFormula, inactive_records).unlink()

        return True
