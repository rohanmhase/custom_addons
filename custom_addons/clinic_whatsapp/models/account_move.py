import logging
from odoo import models

_logger = logging.getLogger(__name__)


class AccountMove(models.Model):
    _inherit = 'account.move'

    def _post(self, soft=True):
        posted = super()._post(soft=soft)

        customer_invoices = posted.filtered(lambda m: m.is_sale_document())
        if not customer_invoices:
            return posted

        template = self.env['whatsapp.template'].search([
            ('model_id.model', '=', 'account.move'),
            ('active', '=', True)
        ], limit=1)

        if not template:
            _logger.info("No active WhatsApp template configured for account.move.")
            return posted

        for move in customer_invoices:
            try:
                if hasattr(move, '_portal_ensure_token'):
                    move._portal_ensure_token()
                template.send_messages(move)
            except Exception as e:
                _logger.exception("Failed queueing WhatsApp notification for invoice %s: %s", move.name, str(e))

        return posted