from odoo import models, fields, api
from odoo.exceptions import AccessError

ADMIN_GROUP = 'mybillbook_invoices.group_mybillbook_admin'


class MybillbookDiscountWizard(models.TransientModel):
    _name = 'mybillbook.discount.wizard'
    _description = 'Calculate Discounts for Existing Invoices'

    total_lines = fields.Integer('Invoice Lines in Database', readonly=True)
    qualify_lines = fields.Integer('Lines That Have a Discount', readonly=True)
    stored_lines = fields.Integer('Lines With Discount Already Saved', readonly=True)

    @api.model
    def default_get(self, fields_list):
        res = super().default_get(fields_list)
        total, qualify, stored = self.env['mybillbook.invoice.line'].discount_stats()
        res.update(total_lines=total, qualify_lines=qualify, stored_lines=stored)
        return res

    def action_calculate(self):
        self.ensure_one()
        if not self.env.user.has_group(ADMIN_GROUP):
            raise AccessError("Only a My Bill Book Administrator can calculate discounts.")
        changed = self.env['mybillbook.invoice.line'].recalculate_all_discounts()
        total, qualify, stored = self.env['mybillbook.invoice.line'].discount_stats()
        return {
            'type': 'ir.actions.client',
            'tag': 'display_notification',
            'params': {
                'title': 'My Bill Book Discounts',
                'message': "Done. %d lines updated. %d of %d lines now carry a discount." % (changed, stored, total),
                'type': 'success',
                'sticky': True,
                'next': {'type': 'ir.actions.act_window_close'},
            },
        }