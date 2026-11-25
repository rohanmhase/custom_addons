# -*- coding: utf-8 -*-
"""Purchase Order raised by the PO team + one-time approval tokens."""
import logging
import secrets
from datetime import timedelta

from odoo import api, fields, models, _
from odoo.exceptions import UserError

_logger = logging.getLogger(__name__)

TOKEN_MAX_DAYS = 7


class SvcTicketPo(models.Model):
    _name = "svcticket.po"
    _description = "Ticket Purchase Order"
    _order = "id desc"

    name = fields.Char(default="/", readonly=True, copy=False, index=True)
    ticket_id = fields.Many2one("svcticket.ticket", required=True, ondelete="cascade", index=True)
    vendor_name = fields.Char()
    amount = fields.Monetary(required=True)
    currency_id = fields.Many2one(related="ticket_id.currency_id", store=True)
    account_details = fields.Text("Quote & Account Details")
    attachment_ids = fields.Many2many("ir.attachment", "svcticket_po_attachment_rel",
                                      "po_id", "attachment_id", "Quote / PO File")
    note = fields.Text()

    approver_ids = fields.Many2many(
        "svcticket.approver", "svcticket_po_approver_rel", "po_id", "approver_id", "Approvers",
        help="Picked from the team's approver registry.")
    requested_by = fields.Many2one("res.users", default=lambda self: self.env.user, readonly=True)
    state = fields.Selection([
        ("draft", "Draft"), ("pending", "Awaiting Approval"),
        ("approved", "Approved"), ("rejected", "Rejected"),
    ], default="draft", required=True, index=True, )
    decided_by = fields.Many2one("res.users", readonly=True)
    decided_at = fields.Datetime(readonly=True)
    remarks = fields.Text("Rejection Remarks")

    token_ids = fields.One2many("svcticket.approval.token", "po_id", "Approval Links")

    @api.model_create_multi
    def create(self, vals_list):
        for vals in vals_list:
            if vals.get("name", "/") == "/":
                vals["name"] = self.env["ir.sequence"].next_by_code("svcticket.po") or "/"
        return super().create(vals_list)

    # ------------------------------------------------------------- actions
    def action_send_for_approval(self):
        self.ensure_one()
        if self.state != "draft":
            raise UserError(_("This PO was already sent for approval."))
        if not self.approver_ids:
            raise UserError(_("Select at least one approver."))
        if not self.amount:
            raise UserError(_("Enter the PO amount."))

        self.state = "pending"
        ticket = self.ticket_id
        row = self.env["svcticket.handoff"]._by_code("send_to_approver")
        row._perform(ticket, approvers=self.approver_ids,
                     note=_("PO %s sent for approval") % self.name)
        ticket._log_event("po_raised", to_lane="approver", note=self.name)

        for approver in self.approver_ids:
            token = self._create_token(approver)
            ticket._mail_po_for_approval(self, approver, self._mail_body(token),
                                         attachment_ids=self.attachment_ids.ids)
            ticket.sudo().message_post(
                body=_("PO <b>%s</b> sent to <b>%s</b> for approval.")
                % (self.name, approver.display_name),
                subtype_xmlid="mail.mt_note", message_type="comment")
        return True

    def _create_token(self, approver):
        """One single-use link per approver record (-> its user).

        No clock-based expiry: the link lives as long as the ticket is still
        waiting for a decision. It dies the moment the PO is approved/rejected
        (or the ticket moves on), see svcticket.approval.token._valid().
        """
        return self.env["svcticket.approval.token"].create({
            "po_id": self.id,
            "user_id": approver.user_id.id,
            "token": secrets.token_urlsafe(32),
        })

    def _mail_body(self, token):
        ticket = self.ticket_id
        base = self.env["ir.config_parameter"].sudo().get_param("web.base.url")
        approve_url = "%s/svcticket/approve/%s" % (base, token.token)
        reject_url = "%s/svcticket/reject/%s" % (base, token.token)
        rows = "".join(
            "<tr><td style='padding:6px 10px;border:1px solid #e3e3e3'><b>%s</b></td>"
            "<td style='padding:6px 10px;border:1px solid #e3e3e3'>%s</td></tr>" % (label, value)
            for label, value in [
                (_("Ticket"), ticket.name), (_("Location"), ticket.location_id.name),
                (_("Vendor"), self.vendor_name or "-"),
                (_("Amount"), "%s %.2f" % (self.currency_id.symbol or "", self.amount)),
                (_("Raised by"), self.requested_by.name),
                (_("Remarks"), self.note or "-"),
            ])
        return _(
            "<div style='font-family:Arial,sans-serif;font-size:14px;color:#222'>"
            "<h3 style='margin:0 0 10px'>PO approval required</h3>"
            "<table style='border-collapse:collapse'>%s</table>"
            "<p style='margin-top:16px'>"
            "<a href='%s' style='background:#0d6efd;color:#fff;padding:10px 18px;"
            "text-decoration:none;border-radius:4px;display:inline-block'>Approve</a>&nbsp;"
            "<a href='%s' style='background:#dc3545;color:#fff;padding:10px 18px;"
            "text-decoration:none;border-radius:4px;display:inline-block'>Reject</a></p>"
            "<p style='color:#666;font-size:12px'>The link stays valid while this "
            "PO is still waiting for a decision.</p></div>"
        ) % (rows, approve_url, reject_url)

    def action_approve(self, user=None, on_call=False):
        self.ensure_one()
        user = user or self.env.user
        if self.state != "pending":
            raise UserError(_("This PO is not awaiting approval."))
        self.sudo().write({"state": "approved", "decided_by": user.id,
                           "decided_at": fields.Datetime.now()})

        ticket = self.ticket_id.sudo()   # the approver is outside the ticket's team
        row = self.env["svcticket.handoff"]._by_code("director_approve")
        row._perform(ticket, note=_("PO %s approved") % self.name)
        ticket._set_state("payment_pending", note=_("PO %s approved - sent to accounts") % self.name)
        ticket._log_event("po_approved", note=self.name)
        ticket._mail_po_decided(self, user, approved=True, on_call=on_call)
        ticket._mail_to_pay(self)
        return True

    def action_approve_on_call(self):
        """The director said yes on the phone - the PO team records it here.

        Same effect as the e-mailed approval, except every mail says it was
        recorded by this user on behalf of the director.
        """
        self.ensure_one()
        if self.state != "pending":
            raise UserError(_("This PO is not awaiting approval."))
        self.ticket_id.sudo().message_post(
            body=_("Approval given on a call for PO <b>%s</b>, recorded by <b>%s</b> "
                   "on behalf of the director.") % (self.name, self.env.user.name),
            subtype_xmlid="mail.mt_note", message_type="comment")
        return self.action_approve(user=self.env.user, on_call=True)

    def action_reject(self, remarks, user=None):
        self.ensure_one()
        user = user or self.env.user
        if not (remarks or "").strip():
            raise UserError(_("Rejection remarks are mandatory."))
        if self.state != "pending":
            raise UserError(_("This PO is not awaiting approval."))
        self.sudo().write({"state": "rejected", "remarks": remarks, "decided_by": user.id,
                           "decided_at": fields.Datetime.now()})

        ticket = self.ticket_id.sudo()   # same reason
        ticket._log_event("po_rejected", note=remarks)
        # A rejected PO is not a dead ticket: it goes back to the facility team
        # so they can rework it, raise a new PO, or close it.
        row = self.env["svcticket.handoff"]._by_code("po_send_back")
        if row:
            row._perform(ticket, note=_("PO %s rejected: %s") % (self.name, remarks))
        else:
            ticket.write({"current_team_id": ticket._team_for_lane("facility"),
                          "current_owner_type": "team"})
            ticket._open_clock("facility", note=_("PO rejected"))
        if ticket.state == "po_pending":
            ticket._set_state("in_progress", note=_("PO %s rejected - back to work") % self.name)
        ticket._mail_po_decided(self, user, approved=False, remarks=remarks)
        return True


class SvcTicketApprovalToken(models.Model):
    _name = "svcticket.approval.token"
    _description = "One-time Approval Link"
    _order = "id desc"

    po_id = fields.Many2one("svcticket.po", required=True, ondelete="cascade", index=True)
    user_id = fields.Many2one("res.users", required=True, ondelete="cascade")
    token = fields.Char(required=True, index=True)
    expires_at = fields.Datetime(
        help="Not used for expiry: a link lives as long as the PO is still "
             "waiting for a decision.")
    used_at = fields.Datetime(readonly=True)

    _sql_constraints = [("token_uniq", "unique(token)", "Token must be unique.")]

    def _valid(self):
        """Valid while the PO is still pending and nobody used it yet.

        No clock expiry: as soon as the PO is approved or rejected - by mail, by
        the PO team on a call, or anywhere else - the link dies with it.
        """
        self.ensure_one()
        return (not self.used_at
                and self.po_id.state == "pending"
                and self.po_id.ticket_id.current_owner_type == "approver")

    def _consume(self):
        self.used_at = fields.Datetime.now()
        self.po_id.token_ids.filtered(lambda t: not t.used_at).used_at = fields.Datetime.now()

    @api.model
    def _find(self, token):
        return self.sudo().search([("token", "=", token)], limit=1)
