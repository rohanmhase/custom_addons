from odoo import fields, models, api, _
from odoo.exceptions import UserError

class ClinicPsmrMapping(models.Model):
    _name = 'clinic.psmr.mapping'
    _description = 'PSMR Clinic Mapping'
    _rec_name = 'psmr_name'  # Overwrites technical model,id string layout inside the breadcrumbs

    psmr_name = fields.Char(string='PSMR Name', required=True)
    pos_config_id = fields.Many2one('pos.config', string='POS Configuration', required=True)


class DailySalesComparison(models.Model):
    _name = 'daily.sales.comparison'
    _description = 'Daily Sales Comparison'
    _order = 'create_date desc'

    name = fields.Char(string='Comparison Title', required=True)
    date_from = fields.Date(string='Start Date', required=True)
    date_to = fields.Date(string='End Date', required=True)
    line_ids = fields.One2many('daily.sales.comparison.line', 'comparison_id', string='Comparison Lines', cascade=True)
    active = fields.Boolean(default=True, tracking=True)

    def unlink(self):
        """ Two-step deletion: Archive first, then permanent drop. """
        is_admin = self.env.user.has_group('account_automation.group_account_automation_admin')
        if not is_admin:
            raise UserError(_("Only Administrators can delete or archive records."))

        records_to_archive = self.filtered(lambda r: r.active)
        records_to_delete = self.filtered(lambda r: not r.active)

        if records_to_archive:
            records_to_archive.write({'active': False})
        if records_to_delete:
            super(DailySalesComparison, records_to_delete).unlink()
        return True


class DailySalesComparisonLine(models.Model):
    _name = 'daily.sales.comparison.line'
    _description = 'Daily Sales Comparison Line'
    _order = 'status desc, difference_abs desc'

    comparison_id = fields.Many2one('daily.sales.comparison', string='Comparison Reference', ondelete='cascade')
    pos_config_id = fields.Many2one('pos.config', string='POS Configuration', readonly=True)
    psmr_name = fields.Char(string='PSMR Name', readonly=True)
    odoo_sales = fields.Float(string='Odoo Sales', readonly=True)
    psmr_sales = fields.Float(string='PSMR Sales', readonly=True)
    difference = fields.Float(string='Difference', readonly=True)
    status = fields.Selection([
        ('matched', 'Matched'),
        ('missing_in_psmr', 'Missing in PSMR'),
        ('unmapped_psmr', 'Unmapped PSMR')
    ], string='Status', readonly=True)
    difference_abs = fields.Float(string='Absolute Difference', compute='_compute_difference_abs', store=True)

    @api.depends('difference')
    def _compute_difference_abs(self):
        for line in self:
            line.difference_abs = abs(line.difference)