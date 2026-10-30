from odoo import models, fields, api


class TelemedicineCustomer(models.Model):
    _name = 'telemedicine.customer'
    _description = 'Telemedicine Customer'
    _rec_name = 'name'

    name = fields.Char(string='Name', required=True)
    external_customer_id = fields.Char(
        string='External Customer ID',
        required=True,
    )
    place_of_supply = fields.Char(string='Place of Supply')
    state_id = fields.Many2one('res.country.state', string='State')
    country_id = fields.Many2one(
        'res.country',
        string='Country',
        default=lambda self: self.env.ref('base.in', raise_if_not_found=False),
    )
    gstin = fields.Char(string='GSTIN')
    invoice_ids = fields.One2many(
        'telemedicine.invoice',
        'customer_id',
        string='Invoices',
    )
    invoice_count = fields.Integer(
        string='Invoice Count',
        compute='_compute_invoice_count',
    )
    company_id = fields.Many2one(
        'res.company',
        string='Company',
        required=True,
        default=lambda self: self.env.company,
    )

    _sql_constraints = [
        ('unique_ext_id_company',
         'unique(external_customer_id, company_id)',
         'A customer with this External ID already exists for this company.'),
    ]

    @api.depends('invoice_ids')
    def _compute_invoice_count(self):
        for rec in self:
            rec.invoice_count = len(rec.invoice_ids)

    def action_view_invoices(self):
        self.ensure_one()
        return {
            'type': 'ir.actions.act_window',
            'name': 'Invoices',
            'res_model': 'telemedicine.invoice',
            'view_mode': 'tree,form',
            'domain': [('customer_id', '=', self.id)],
        }
