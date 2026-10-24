from odoo import models, fields, api, _
from odoo.exceptions import UserError


class OnlineImportConfig(models.Model):
    _name = 'online.import.config'
    _description = 'Online Import Configuration'

    name = fields.Char(default='Online Imports Settings', required=True)
    company_id = fields.Many2one(
        'res.company',
        string='Company',
        required=True,
        default=lambda self: self.env.company
    )
    prepaid_journal_id = fields.Many2one(
        'account.journal',
        string='Prepaid Bank Journal',
        domain="[('type', '=', 'bank'), ('company_id', '=', company_id)]"
    )
    pod_journal_id = fields.Many2one(
        'account.journal',
        string='POD Bank Journal',
        domain="[('type', '=', 'bank'), ('company_id', '=', company_id)]"
    )

    @api.model_create_multi
    def create(self, vals_list):
        for vals in vals_list:
            comp_id = vals.get('company_id') or self.env.company.id
            if self.search_count([('company_id', '=', comp_id)]) >= 1:
                raise UserError(_("Configuration already exists for this company."))
        return super().create(vals_list)