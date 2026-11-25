# -*- coding: utf-8 -*-
"""Small popups: request hold, reject hold, record payment, submit invoice."""
from odoo import api, fields, models, _
from odoo.exceptions import UserError, ValidationError


class SvcTicketHoldWizard(models.TransientModel):
    """Two entry points: ask for a Purchase Order, or just park the ticket.

    Raise PO   -> hold goes to the team lead, then the ticket lands on the PO
                  team, who attach the quote + account details and raise the PO.
    Other hold -> hold goes to the team lead, then the SLA clock pauses.
    """
    _name = "svcticket.hold.wizard"
    _description = "Request Hold"

    ticket_id = fields.Many2one("svcticket.ticket", required=True, readonly=True)
    pre_hold_state = fields.Char(readonly=True)

    request_type = fields.Selection([
        ("po", "Raise PO / Purchase Request"),
        ("other", "Other Hold"),
    ], default="other", required=True)

    reason_id = fields.Many2one(
        "svcticket.hold.reason", "Reason",
        domain="[('active','=',True)]",
        help="Optional for a PO request, required for any other hold.")

    po_number = fields.Char("PO / Reference")
    estimated_cost = fields.Monetary(currency_field="currency_id")
    currency_id = fields.Many2one(related="ticket_id.currency_id")

    approver_ids = fields.Many2many(
        "svcticket.approver", string="Approvers",
        compute="_compute_approvers", store=True, readonly=False,
        help="Pre-filled from the team's approver registry.")

    note = fields.Text("Justification", required=True)

    @api.depends("ticket_id")
    def _compute_approvers(self):
        for wizard in self:
            wizard.approver_ids = self.env["svcticket.approver"].search([("active", "=", True)])

    @api.onchange("request_type")
    def _onchange_request_type(self):
        if self.request_type == "po":
            po_reason = self.env["svcticket.hold.reason"].search(
                [("needs_financials", "=", True)], limit=1)
            if po_reason:
                self.reason_id = po_reason
        elif self.reason_id and self.reason_id.needs_financials:
            self.reason_id = False

    def action_confirm(self):
        self.ensure_one()
        is_po = self.request_type == "po"
        if not is_po and not self.reason_id:
            raise UserError(_("Pick a hold reason."))
        if is_po and not self.approver_ids:
            raise UserError(_("A PO request needs at least one approver."))

        ticket = self.ticket_id
        ticket.write({
            "hold_needs_po": is_po,
            "hold_reason_id": self.reason_id.id,
            "hold_po_number": self.po_number if is_po else False,
            "hold_cost": self.estimated_cost if is_po else 0.0,
            "hold_approver_ids": [(6, 0, self.approver_ids.ids)],
            "hold_note": self.note,
            "pre_hold_state": self.pre_hold_state or ticket.state,
        })
        row = ticket.env["svcticket.handoff"]._by_code("hold_to_lead")
        row._perform(ticket, note=self.note)
        # the team's clock keeps running with its own deadline; the lead gets a
        # second clock (per-priority limit) to decide in time
        ticket._open_vetting_clock_for_hold(row)
        ticket._set_state("hold_requested",
                          note=_("PO request") if is_po else _("Hold requested"))
        ticket._log_event("hold_requested",
                          note=self.reason_id.name or ("PO request" if is_po else "-"))

        # event 8: the team lead is the only one told. The picked approver is
        # only mailed later, when the PO team presses "Send to Approver".
        ticket._mail_hold_requested(is_po, self.note)
        return {"type": "ir.actions.act_window_close"}


class SvcTicketPoWizard(models.TransientModel):
    """PO team fills this instead of creating a PO in Odoo - the real PO lives
    in the purchase module; here we only need what goes into the approval mail.
    """
    _name = "svcticket.po.wizard"
    _description = "Send PO for Approval"

    ticket_id = fields.Many2one("svcticket.ticket", required=True, readonly=True)
    vendor_name = fields.Char("Vendor", required=True)
    amount = fields.Monetary(required=True, currency_field="currency_id")
    currency_id = fields.Many2one(related="ticket_id.currency_id")
    po_reference = fields.Char("PO Number / Reference")
    approver_ids = fields.Many2many(
        "svcticket.approver", string="Send To", required=True,
        help="Anyone from the approver directory.")
    note = fields.Text("Justification")
    attachment_ids = fields.Many2many("ir.attachment", string="PO Document (PDF)")

    @api.model
    def default_get(self, fields_list):
        """Whoever was picked on the hold request is the one who gets mailed -
        and only now, when the PO team actually presses "Send to Approver"."""
        res = super().default_get(fields_list)
        if "approver_ids" in fields_list and not res.get("approver_ids"):
            ticket = self.env["svcticket.ticket"].browse(res.get("ticket_id") or 0).exists()
            if ticket:
                res["approver_ids"] = [(6, 0, ticket.hold_approver_ids.ids)]
        return res

    def action_send(self):
        self.ensure_one()
        if not self.attachment_ids:
            raise UserError(_("Attach the PO document (PDF) before sending for approval."))
        self.ticket_id._own_attachments(self.attachment_ids)
        po = self.env["svcticket.po"].create({
            "ticket_id": self.ticket_id.id,
            "vendor_name": self.vendor_name,
            "amount": self.amount,
            "note": self.note or self.po_reference,
            "account_details": self.po_reference,
            "approver_ids": [(6, 0, self.approver_ids.ids)],
            "attachment_ids": [(6, 0, self.attachment_ids.ids)],
        })
        po.action_send_for_approval()
        return {"type": "ir.actions.act_window_close"}


class SvcTicketHoldRejectWizard(models.TransientModel):
    _name = "svcticket.hold.reject.wizard"
    _description = "Reject Hold"

    ticket_id = fields.Many2one("svcticket.ticket", required=True, readonly=True)
    rejection_note = fields.Text("Reason", required=True)

    def action_confirm(self):
        self.ensure_one()
        ticket = self.ticket_id
        if not ticket._can_vet_hold():
            raise UserError(_("Only the team lead can reject a hold."))
        ticket._close_vetting_clock("hold rejected")
        ticket.write({"hold_reject_note": self.rejection_note, "hold_needs_po": False})
        ticket._log_event("hold_rejected", note=self.rejection_note)
        # back to the team that asked, marked Rejected
        self.env["svcticket.handoff"]._by_code("lead_reject")._perform(
            ticket, note=self.rejection_note)
        ticket.message_post(body=_("Hold rejected: %s") % self.rejection_note)
        return {"type": "ir.actions.act_window_close"}


class SvcTicketPaymentWizard(models.TransientModel):
    _name = "svcticket.payment.wizard"
    _description = "Record Payment"

    ticket_id = fields.Many2one("svcticket.ticket", required=True, readonly=True)
    amount = fields.Monetary(required=True, currency_field="currency_id")
    deduction_amount = fields.Monetary("Deduction (TDS / etc.)", currency_field="currency_id", default=0.0,
        help="Amount deducted from the payment (e.g. TDS). The PO obligation is "
        "settled by the gross `amount`; only the net (amount - deduction) leaves the bank.")
    currency_id = fields.Many2one(related="ticket_id.currency_id")
    amount_due = fields.Monetary(related="ticket_id.amount_due")
    po_id = fields.Many2one("svcticket.po", "Against PO",
                            domain="[('ticket_id','=',ticket_id),('state','=','approved')]",
                            compute="_compute_po_id", store=False, readonly=False,
                            help="The approved PO this payment settles.")
    tender_type = fields.Selection([
        ("neft", "NEFT"), ("rtgs", "RTGS"), ("upi", "UPI"), ("cheque", "Cheque"),
        ("cash", "Cash"), ("card", "Card"), ("other", "Other"),
    ], default="neft", required=True)
    reference = fields.Char("UTR / Transaction #", required=True)
    paid_at = fields.Date("Paid On", default=fields.Date.context_today, required=True)
    proof_ids = fields.Many2many("ir.attachment", string="Proof of Payment")
    note = fields.Char()

    @api.depends("ticket_id")
    def _compute_po_id(self):
        for wizard in self:
            approved = wizard.ticket_id.po_ids.filtered(lambda p: p.state == "approved")
            wizard.po_id = approved[:1]

    @api.constrains("amount", "ticket_id")
    def _check_amount(self):
        for wizard in self:
            ticket = wizard.ticket_id
            if ticket.amount_total and wizard.amount > ticket.amount_due + 0.005:
                raise ValidationError(_(
                    "You cannot pay %(paid).2f on %(ticket)s. The approved PO is "
                    "%(total).2f and only %(due).2f is left to pay.",
                    paid=wizard.amount, ticket=ticket.name,
                    total=ticket.amount_total, due=ticket.amount_due))

    def action_confirm(self):
        self.ensure_one()
        ticket = self.ticket_id
        # 1. the money can never be more than what is still owed
        if self.amount <= 0:
            raise UserError(_("Enter an amount greater than zero."))
        if ticket.amount_total and self.amount > ticket.amount_due + 0.005:
            raise UserError(_(
                "You cannot pay %(paid).2f on %(ticket)s. The approved PO is "
                "%(total).2f and only %(due).2f is left to pay.",
                paid=self.amount, ticket=ticket.name,
                total=ticket.amount_total, due=ticket.amount_due))
        # 2. nothing may be paid while a PO is still waiting for its approval
        waiting = ticket.po_ids.filtered(lambda p: p.state == "pending")
        if waiting:
            raise UserError(_(
                "PO %s is still waiting for approval. Nothing can be paid on this "
                "ticket until it is approved.") % ", ".join(waiting.mapped("name")))
        if not ticket.po_ids.filtered(lambda p: p.state == "approved") \
                and not self.env.context.get("svcticket_allow_no_po"):
            raise UserError(_(
                "There is no approved PO on this ticket, so there is nothing to pay. "
                "Record the money only against an approved PO."))
        ticket._own_attachments(self.proof_ids)
        payment = self.env["svcticket.payment"].create({
            "ticket_id": ticket.id,
            "po_id": self.po_id.id or ticket.po_ids.filtered(
                lambda p: p.state == "approved")[:1].id,
            "amount": self.amount,
            "deduction_amount": self.deduction_amount,
            "tender_type": self.tender_type,
            "reference": self.reference,
            "paid_at": self.paid_at,
            "proof_ids": [(6, 0, self.proof_ids.ids)],
            "note": self.note,
        })
        payment._archive_proofs()
        ticket._log_event("payment_recorded", from_lane="payment",
                          note="%s %s" % (self.tender_type, self.reference))
        # Back to the facility team: finish the job or invoice the balance.
        row = self.env["svcticket.handoff"]._by_code("payment_done")
        row._perform(ticket, note="%s %s" % (self.tender_type, self.reference))
        ticket._mail_payment_recorded(self.amount, self.reference, self.tender_type)
        return {"type": "ir.actions.act_window_close"}


class SvcTicketAddedForPaymentWizard(models.TransientModel):
    _name = "svcticket.added.for.payment.wizard"
    _description = "Added for Payment"

    ticket_id = fields.Many2one("svcticket.ticket", required=True, readonly=True)
    # proof is compulsory - this is the team's evidence that the payout was
    # initiated (e.g. screenshot of the email / payment intimation)
    proof_ids = fields.Many2many(
        "ir.attachment", string="Proof of Payment Initiation", required=True)
    note = fields.Char("Note")

    def action_confirm(self):
        self.ensure_one()
        ticket = self.ticket_id
        ticket._own_attachments(self.proof_ids)
        ticket.write({"payment_added_at": fields.Datetime.now(),
                      "payment_added_by": self.env.user.id})
        row = self.env["svcticket.handoff"]._by_code("added_for_payment")
        row._perform(ticket)
        ticket._log_event("payment_added", from_lane="payment",
                          note=_("Accounts team picked it up"))
        ticket._mail_added_for_payment()
        ticket.message_post(body=_("Added for payment by %s. Proof attached.")
                            % self.env.user.name)
        return {"type": "ir.actions.act_window_close"}


class SvcTicketBalanceWaiveWizard(models.TransientModel):
    _name = "svcticket.balance.waive.wizard"
    _description = "Waive Remaining Balance"

    ticket_id = fields.Many2one("svcticket.ticket", required=True, readonly=True)
    currency_id = fields.Many2one(related="ticket_id.currency_id")
    waive_amount = fields.Monetary(required=True)
    reason = fields.Text("Reason", required=True,
                         help="Why the remaining balance is written off - kept for audit.")

    def action_confirm(self):
        self.ensure_one()
        ticket = self.ticket_id
        if self.waive_amount <= 0:
            raise UserError(_("Enter an amount greater than zero."))
        if self.waive_amount > ticket.amount_due + 0.005:
            raise UserError(_(
                "Cannot waive %(waive).2f - the remaining balance on %s is only "
                "%(due).2f.") % {"waive": self.waive_amount, "ticket": ticket.name,
                                 "due": ticket.amount_due})
        ticket.write({"amount_waived": ticket.amount_waived + self.waive_amount})
        ticket._log_event("balance_waived", note=self.reason)
        ticket.message_post(body=_("Balance waived: %(amount).2f. Reason: %(reason)s")
                            % {"amount": self.waive_amount, "reason": self.reason})
        return {"type": "ir.actions.act_window_close"}


class SvcTicketInvoiceRejectWizard(models.TransientModel):
    _name = "svcticket.invoice.reject.wizard"
    _description = "Reject Tax Invoice"

    ticket_id = fields.Many2one("svcticket.ticket", required=True, readonly=True)
    reason = fields.Text(required=True)

    def action_confirm(self):
        self.ensure_one()
        ticket = self.ticket_id
        ticket.write({
            "invoice_accepted": False,
            "invoice_attachment_ids": [(5, 0, 0)],
            "invoice_number": False,
            "invoice_reject_count": ticket.invoice_reject_count + 1,
            "invoice_reject_reason": self.reason,
            # back with the facility team so they can submit a corrected invoice
            "state": "in_progress",
        })
        ticket.message_post(body=_(
            "Tax invoice <b>rejected</b> by %s.<br/>Reason: %s") % (
            self.env.user.name, self.reason))
        ticket._log_event("invoice", note=_("rejected - %s") % self.reason)
        row = self.env["svcticket.handoff"]._by_code("invoice_rejected")
        row._perform(ticket, note=self.reason)
        ticket._mail_invoice_rejected(self.reason)
        return {"type": "ir.actions.act_window_close"}


class SvcTicketInvoiceWizard(models.TransientModel):
    _name = "svcticket.invoice.wizard"
    _description = "Submit Tax Invoice"

    ticket_id = fields.Many2one("svcticket.ticket", required=True, readonly=True)
    invoice_number = fields.Char(required=True)
    amount = fields.Monetary(related="ticket_id.amount_due")
    currency_id = fields.Many2one(related="ticket_id.currency_id")
    attachment_ids = fields.Many2many("ir.attachment", string="Invoice File")
    note = fields.Char()

    def action_confirm(self):
        self.ensure_one()
        if not self.attachment_ids:
            raise UserError(_(
                "Attach the invoice PDF. Without a file the accounts team has "
                "nothing to accept, and the ticket can never be resolved."))
        ticket = self.ticket_id
        ticket._own_attachments(self.attachment_ids)
        ticket.write({
            "invoice_number": self.invoice_number,
            "invoice_attachment_ids": [(6, 0, self.attachment_ids.ids)],
        })
        from .s3_storage import upload_attachments
        upload_attachments(self.env, ticket, self.attachment_ids, "invoice")
        ticket._log_event("invoice", note="%s / %s" % (self.invoice_number, self.note or ""))
        ticket.write({"invoice_accepted": False})
        row = ticket.env["svcticket.handoff"]._by_code("submit_invoice")
        row._perform(ticket, note=self.invoice_number)
        ticket._set_state("payment_pending", note=_("Tax invoice submitted"))
        ticket._mail_invoice_submitted(self.invoice_number)
        return {"type": "ir.actions.act_window_close"}


class SvcTicketRejectWizard(models.TransientModel):
    """Rejecting the ticket itself - the reporter is told why."""
    _name = "svcticket.reject.wizard"
    _description = "Reject Ticket"

    ticket_id = fields.Many2one("svcticket.ticket", required=True, readonly=True)
    reason = fields.Text("Reason for rejecting", required=True)

    def action_confirm(self):
        self.ensure_one()
        self.ticket_id.action_do_reject_ticket(self.reason)
        return {"type": "ir.actions.act_window_close"}
