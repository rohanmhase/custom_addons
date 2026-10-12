import logging
from odoo import models

_logger = logging.getLogger(__name__)

class AccountMove(models.Model):
    _inherit = 'account.move'

    def _post(self, soft=True):
        posted = super()._post(soft=soft)
        customer_invoices = posted.filtered(
            lambda m: m.is_sale_document() and m.state == 'posted'
        )
        if not customer_invoices:
            return posted

        # Dynamically trigger any template configured for account.move with auto_send_on_post enabled
        auto_templates = self.env['whatsapp.template'].search([
            ('model_id.model', '=', 'account.move'),
            ('auto_send_on_post', '=', True),
            ('active', '=', True),
        ])

        for template in auto_templates:
            for move in customer_invoices:
                try:
                    template.send_messages(move)
                except Exception as e:
                    _logger.exception("Auto WhatsApp dispatch failed for invoice %s: %s", move.name, str(e))

        return posted