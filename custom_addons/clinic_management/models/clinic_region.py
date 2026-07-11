from odoo import models, fields, api

class ClinicRegion(models.Model):
    _name = "clinic.region"
    _description = "Clinic Region"

    name = fields.Char(required=True)