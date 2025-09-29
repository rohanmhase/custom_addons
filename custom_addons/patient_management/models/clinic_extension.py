from odoo import models, fields

class Clinic(models.Model):
    _inherit = "clinic.clinic"

    patient_ids = fields.One2many('clinic.patient', 'clinic_id', string="Patients")
