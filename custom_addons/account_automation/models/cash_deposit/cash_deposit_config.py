from odoo import models, fields


class CashDepositClinicMapping(models.Model):
    _name = 'cash.deposit.clinic.mapping'
    _description = 'CSV Raw Clinic Name to Odoo Clinic Mapping'
    _rec_name = 'raw_clinic_name'

    raw_clinic_name = fields.Char(
        string='CSV Raw Clinic Name',
        required=True,
        help="Exact text that appears in the 'Clinic' column of the bank CSV, e.g. 'Dwarka'."
    )
    clinic_id = fields.Many2one(
        'clinic.clinic',
        string='Odoo Clinic',
        required=True
    )
    active = fields.Boolean(default=True)