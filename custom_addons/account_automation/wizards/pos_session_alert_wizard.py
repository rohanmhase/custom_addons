from odoo import models, fields, api, _
from datetime import timedelta


class PosSessionAlertWizard(models.TransientModel):
    _name = 'pos.session.alert.wizard'
    _description = 'Manual POS Session Alert Trigger'

    check_date = fields.Date(
        string='Business Date',
        required=True,
        default=lambda self: fields.Date.context_today(self) - timedelta(days=1)
    )
    force_resend = fields.Boolean(
        string='Force Resend',
        default=False,
        help="If checked, re-sends alerts even if already sent for this date."
    )

    @api.constrains('check_date')
    def _check_date_not_future(self):
        today = fields.Date.context_today(self)
        for rec in self:
            if rec.check_date and rec.check_date > today:
                from odoo.exceptions import ValidationError
                raise ValidationError(_("Business Date cannot be in the future."))

    def action_run(self):
        self.ensure_one()
        stats = self.env['pos.session.alert.service'].process_alerts_for_date(
            self.check_date, force_resend=self.force_resend
        )
        return {
            'type': 'ir.actions.client',
            'tag': 'display_notification',
            'params': {
                'title': _('Alert Check Complete'),
                'message': _('Processed: %(processed)s | Emails sent: %(sent)s | Errors: %(errors)s') % {
                    'processed': stats['processed'],
                    'sent': stats['emails_sent'],
                    'errors': stats['errors'],
                },
                'type': 'success',
                'sticky': False,
            }
        }