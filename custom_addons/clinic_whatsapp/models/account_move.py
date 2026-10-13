import logging
from odoo import models

_logger = logging.getLogger(__name__)

class AccountMove(models.Model):
    _inherit = 'account.move'

    def _post(self, soft=True):
        posted = super()._post(soft=soft)

        # Only target customer invoices (out_invoice), ignoring credit notes (out_refund)
        customer_invoices = posted.filtered(
            lambda m: m.move_type == 'out_invoice' and m.state == 'posted'
        )
        if not customer_invoices:
            return posted

        # Query using .sudo() and the stored 'model' field to bypass ir.model access checks
        auto_templates = self.env['whatsapp.template'].sudo().search([
            ('model', '=', 'account.move'),
            ('auto_send_on_post', '=', True),
            ('active', '=', True),
        ])

        for template in auto_templates:
            for move in customer_invoices:
                try:
                    template.sudo().send_messages(move)
                except Exception as e:
                    _logger.exception(
                        "Auto WhatsApp dispatch failed for invoice %s: %s",
                        move.name,
                        str(e),
                    )

        return posted