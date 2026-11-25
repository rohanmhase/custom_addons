# -*- coding: utf-8 -*-
"""Payments recorded by the payment team (partial or full)."""
from odoo import _, api, fields, models
from odoo.exceptions import ValidationError


class SvcTicketPayment(models.Model):
    _name = "svcticket.payment"
    _description = "Ticket Payment"
    _order = "id desc"

    ticket_id = fields.Many2one("svcticket.ticket", required=True, ondelete="cascade", index=True)
    po_id = fields.Many2one("svcticket.po", "Against PO", ondelete="set null")
    amount = fields.Monetary(required=True)
    deduction_amount = fields.Monetary("Deduction", default=0.0,
        help="Amount deducted from this payment (e.g. TDS).")
    currency_id = fields.Many2one(related="ticket_id.currency_id", store=True)
    tender_type = fields.Selection([
        ("neft", "NEFT"), ("rtgs", "RTGS"), ("upi", "UPI"),
        ("cheque", "Cheque"), ("cash", "Cash"), ("card", "Card"), ("other", "Other"),
    ], default="neft", required=True)
    reference = fields.Char("UTR / Transaction #", required=True)
    paid_at = fields.Date("Paid On", default=fields.Date.context_today, required=True,
                          help="The day the money left the bank. Date only.")
    proof_ids = fields.Many2many("ir.attachment", "svcticket_payment_proof_rel",
                                 "payment_id", "attachment_id", "Proof of Payment")
    note = fields.Char()
    user_id = fields.Many2one("res.users", default=lambda self: self.env.user, readonly=True)
    state = fields.Selection([
        ("recorded", "Recorded"), ("verified", "Verified"),
    ], default="recorded", required=True, index=True)

    @api.constrains("amount", "ticket_id", "paid_at")
    def _check_amount(self):
        for payment in self:
            ticket = payment.ticket_id
            if payment.amount <= 0:
                raise ValidationError(_("A payment must be more than zero."))
            # the approved PO is the ceiling: never let a payment overshoot it
            due = ticket.amount_due + payment.amount
            if ticket.amount_total and payment.amount > due + 0.005:
                raise ValidationError(_(
                    "You cannot pay %(paid).2f on %(ticket)s. The approved PO is "
                    "%(total).2f and the balance left is %(due).2f.",
                    paid=payment.amount, ticket=ticket.name,
                    total=ticket.amount_total, due=due))

    def _auto_init(self):
        """Upgrades: `paid_at` changed from a timestamp to a plain date."""
        res = super()._auto_init()
        self.env.cr.execute("""
            SELECT data_type FROM information_schema.columns
            WHERE table_name = 'svcticket_payment' AND column_name = 'paid_at'
        """)
        row = self.env.cr.fetchone()
        if row and row[0] in ("timestamp without time zone", "timestamp with time zone"):
            self.env.cr.execute("""
                ALTER TABLE svcticket_payment
                ALTER COLUMN paid_at TYPE date USING paid_at::date
            """)
        return res

    def _archive_proofs(self):
        from .s3_storage import upload_attachments
        for payment in self:
            if payment.proof_ids:
                upload_attachments(payment.env, payment.ticket_id, payment.proof_ids, "payment")

    def action_verify(self):
        self.write({"state": "verified"})
        for payment in self:
            payment.ticket_id._log_event("payment_recorded", note="verified %s" % payment.reference)
        return True
