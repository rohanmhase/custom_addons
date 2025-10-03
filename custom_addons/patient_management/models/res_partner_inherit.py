from odoo import models, fields


class ResPartner(models.Model):
    _inherit = 'res.partner'

    clinic_id = fields.Many2one(
        "clinic.clinic",
        string="Clinic")