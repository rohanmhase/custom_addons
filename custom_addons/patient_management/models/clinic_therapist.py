from odoo import api, fields, models, _
from odoo.exceptions import ValidationError
import re

class ClinicTherapist(models.Model):
    _name = 'clinic.therapist'
    _description = ''
    name = fields.Char(string="Therapist Name", required=True)
    clinic_id = fields.Many2one('clinic.clinic', string="Clinic", readonly= True)
    contact_number = fields.Char(string="Contact Number", size = 10, required=True)

    _sql_constraints = [
        ('unique_contact_per_clinic',
         'unique(contact_number, clinic_id)',
         'This contact number is already registered for another therapist in this clinic!')
    ]

    @api.constrains('contact_number')
    def _check_phone_number(self):
        for rec in self:
            if rec.contact_number:
                # Only allow exactly 10 digits
                if not re.match(r'^\d{10}$', rec.contact_number):
                    raise ValidationError("Phone number must be exactly 10 digits and contain only numbers.")
                elif len(set(rec.contact_number))==1:
                    raise ValidationError("All Digits can not be same")


