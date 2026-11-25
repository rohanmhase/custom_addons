# -*- coding: utf-8 -*-
"""Notification channels + one place that actually sends things."""
import json
import logging

from odoo import api, fields, models

_logger = logging.getLogger(__name__)


class SvcTicketChannel(models.Model):
    _name = "svcticket.channel"
    _description = "Notification Channel"
    _order = "sequence, id"

    name = fields.Char(required=True)
    code = fields.Char(required=True, help="email, inapp, whatsapp, sms")
    provider = fields.Char()
    is_active = fields.Boolean(default=True)
    sequence = fields.Integer(default=10)
    config_data = fields.Text("Configuration (JSON)", help='{"api_key": "...", "from": "..."}')

    _sql_constraints = [("code_uniq", "unique(code)", "Channel code must be unique.")]

    @api.model
    def _active_codes(self):
        return set(self.search([("is_active", "=", True)]).mapped("code")) or {"email", "inapp"}

    @api.model
    def notify(self, ticket, partners, subject, body, codes=None):
        """In-app notification only - NEVER an e-mail.

        `mail.mt_comment` makes Odoo queue a real mail behind your back, which
        would break the agreed recipient list (two mails for one event, and
        mails where there should be none). Every real e-mail goes through
        `send_mail()` above, exactly once, with a named audience.
        """
        if not partners:
            return
        codes = set(codes or self._active_codes())
        if ticket and codes & {"email", "inapp"}:
            # no partner_ids on purpose: with recipients Odoo creates a
            # notification mail.mail row behind our back, which then shows up
            # as "Outgoing Mail" noise. The chatter note is enough here - the
            # real mail (if this event has one) goes out via send_mail().
            ticket.message_post(body=body, subject=subject,
                                subtype_xmlid="mail.mt_note",
                                message_type="notification")
        for code in codes - {"email", "inapp"}:
            self._send_external(code, partners, subject, body)

    @api.model
    def send_mail(self, ticket, partners, subject, body, attachment_ids=None):
        """Send a real e-mail right now, through the company's outgoing server.

        - sent immediately (`sudo().send()`), not left in the queue cron
        - `auto_delete = False`: the mail.mail record stays, so you can always
          open Settings -> Technical -> Outgoing Mails and see what went out,
          to whom, and whether it was sent or raised an error
        - a failure is logged and never breaks the workflow
        """
        partners = partners.filtered(lambda p: p.email)
        if not partners:
            _logger.warning("No e-mail address for %s - mail skipped: %s",
                            partners, subject)
            return self.env["mail.mail"]
        company = self.env.company
        values = {
            "subject": subject,
            "body_html": body,
            "partner_ids": [(6, 0, partners.ids)],
            "email_to": ", ".join(p.email for p in partners),
            "email_from": company.email_formatted or self.env.user.email_formatted
            or False,
            "reply_to": company.email or self.env.user.email_formatted or False,
            "model": ticket._name if ticket else False,
            "res_id": ticket.id if ticket else False,
            # nothing may delete the record after sending - it is the proof
            "auto_delete": False,
            "state": "outgoing",
        }
        if attachment_ids:
            values["attachment_ids"] = [(6, 0, list(attachment_ids))]
        mail = self.env["mail.mail"].sudo().create(values)
        try:
            # sudo + immediate: uses whatever SMTP is configured for the company
            mail.sudo().send(raise_exception=False)
        except Exception:                     # noqa: BLE001
            _logger.exception("Mail could not be sent: %s", subject)
        return mail

    @api.model
    def _send_external(self, code, partners, subject, body):
        channel = self.search([("code", "=", code), ("is_active", "=", True)], limit=1)
        if not channel:
            return
        try:
            payload = json.loads(channel.config_data or "{}")
        except ValueError:
            payload = {}
        if not payload.get("api_key"):
            _logger.warning("Channel %s has no api_key configured - skipping.", code)
            return
        # Adapter hook: fill in the provider call here (Meta Business API / Twilio).
        _logger.info("Channel %s -> %s partners: %s", code, len(partners), subject)
