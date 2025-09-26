from odoo import api, fields, models

class StockWarehouse(models.Model):
    _inherit = "stock.warehouse"
    clinic_id = fields.Many2one("clinic.clinic", string="Clinic")

class PosConfig(models.Model):
    _inherit = "pos.config"
    clinic_id = fields.Many2one("clinic.clinic", string="Clinic")