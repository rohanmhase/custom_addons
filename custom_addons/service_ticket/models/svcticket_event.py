# -*- coding: utf-8 -*-
"""Append-only audit trail: who moved the ticket, when, from where to where."""
import logging

from odoo import fields, models

_logger = logging.getLogger(__name__)

EVENT_TYPES = [
    ("created", "Created"),
    ("state", "State Change"),
    ("handoff", "Lane Hand-off"),
    ("sla_start", "SLA Started"),
    ("sla_pause", "SLA Paused"),
    ("sla_resume", "SLA Resumed"),
    ("sla_breach", "SLA Breach"),
    ("hold_requested", "Hold Requested"),
    ("hold_approved", "Hold Approved"),
    ("hold_rejected", "Hold Rejected"),
    ("rejected", "Rejected"),
    ("po_raised", "PO Raised"),
    ("po_approved", "PO Approved"),
    ("po_rejected", "PO Rejected"),
    ("payment_added", "Added for Payment"),
    ("payment_recorded", "Payment Recorded"),
    ("invoice", "Tax Invoice Submitted"),
    ("resolved", "Resolved"),
    ("closed", "Closed"),
    ("reopened", "Reopened"),
    ("assigned", "Assigned"),
    ("note", "Note"),
]


class SvcTicketEvent(models.Model):
    _name = "svcticket.event"
    _description = "Service Ticket Timeline Entry"
    _order = "at desc, id desc"

    ticket_id = fields.Many2one("svcticket.ticket", required=True, ondelete="cascade", index=True)
    event_type = fields.Selection(EVENT_TYPES, required=True, index=True)
    at = fields.Datetime(default=fields.Datetime.now, required=True, index=True)
    user_id = fields.Many2one("res.users", default=lambda self: self.env.user, required=True)
    from_lane = fields.Selection([
        ("facility", "Facility Team"), ("po", "PO Team"),
        ("approver", "Approver"), ("payment", "Payment Team"),
    ], string="From Lane")
    to_lane = fields.Selection([
        ("facility", "Facility Team"), ("po", "PO Team"),
        ("approver", "Approver"), ("payment", "Payment Team"),
        ("lead", "Team Lead"),
    ], string="To Lane")
    note = fields.Char()

    def init(self):
        self.env.cr.execute("""
            CREATE INDEX IF NOT EXISTS svcticket_event_ticket_idx
            ON svcticket_event (ticket_id, at DESC)
        """)
