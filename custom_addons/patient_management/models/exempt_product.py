from odoo import models, fields, api, _
from odoo.exceptions import UserError
from datetime import datetime, timedelta, date

class ExemptProduct(models.Model):
    _name = "prescription.exempt.product"
    _description = "Exempted Products from Follow-up"

    product_id = fields.Many2one(
        "product.product",
        string="Exempted Medicine",
        required=True,
        domain=[('sale_ok', '=', True)]
    )
    active = fields.Boolean(default=True)

    _sql_constraints = [
        ('unique_product', 'unique(product_id)', 'This medicine is already in the exempted list!')
    ]
