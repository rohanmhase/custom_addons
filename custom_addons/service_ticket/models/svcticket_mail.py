# -*- coding: utf-8 -*-
"""Every outgoing e-mail in one place.

The module used to only write chatter notes, so nothing ever reached a mailbox.
Each event below builds a small HTML mail and queues it as a real `mail.mail`
row (the standard queue cron delivers it).

Recipients are described in words, so the routing stays readable:

    "reporter"        the person who raised it
    "team"            the home (facility) team - members + leads
    "holder"          whoever holds it right now
    "lead"            the lead of the team holding it
    "po"              the PO team - members + leads
    "accounts"        the accounts team - members + leads
    "approvers"       the approvers of the category (genuine check)
    "po_approvers"    the approvers picked on the PO
    "veto:<name>"     drop that account: e.g. "team,-reporter"
"""
import logging

from odoo import api, fields, models, _

_logger = logging.getLogger(__name__)

TICKET_URL = "/web#id=%s&model=svcticket.ticket&view_type=form"


class SvcTicketMail(models.Model):
    _inherit = "svcticket.ticket"

    # ----------------------------------------------------------------- teams
    def _team_by_type(self, team_type):
        return self.env["svcticket.team"].sudo().search(
            [("team_type", "=", team_type), ("active", "=", True)], limit=1)

    def _team_people(self, team):
        team = team.sudo()
        return (team.member_ids | team.lead_ids).mapped("partner_id")

    def _recipients(self, *tokens):
        """Turn the words above into a partner recordset (never raises)."""
        self.ensure_one()
        partners = self.env["res.partner"]
        for token in tokens:
            drop = token.startswith("-")
            token = token.lstrip("-+")
            found = self.env["res.partner"]
            if token == "reporter":
                found = self.reporter_id.partner_id
            elif token in ("team", "facility"):
                found = self._team_people(self.team_id)
            elif token == "holder":
                found = self.current_team_id and self._team_people(self.current_team_id) \
                    or self.current_user_id.partner_id
            elif token == "lead":
                team = self.current_team_id or self.team_id
                found = team.sudo().lead_ids.mapped("partner_id")
            elif token == "po":
                found = self._team_people(self._team_by_type("po_team"))
            elif token == "accounts":
                found = self._team_people(self._team_by_type("account_team"))
            elif token == "approvers":
                found = self._approval_approvers().mapped("user_id.partner_id")
            elif token == "po_approvers":
                found = self.po_ids.mapped("approver_ids.user_id.partner_id")
            partners = (partners - found) if drop else (partners | found)
        return partners.filtered(lambda p: p.email)

    # --------------------------------------------------------------- content
    def _mail_url(self):
        self.ensure_one()
        base = self.env["ir.config_parameter"].sudo().get_param("web.base.url", "")
        return (base or "") + TICKET_URL % self.id

    def _mail_html(self, intro, rows=None, links=None, footer=None):
        self.ensure_one()
        cells = "".join(
            "<tr><td style='padding:6px 10px;border:1px solid #e3e3e3'>%s</td>"
            "<td style='padding:6px 10px;border:1px solid #e3e3e3'><b>%s</b></td></tr>"
            % (label, value if value not in (None, "") else "-")
            for label, value in (rows or []))
        table = ("<table style='border-collapse:collapse;margin:8px 0'>%s</table>" % cells
                 if cells else "")
        buttons = ""
        for label, url, colour in (links or []):
            buttons += ("<a href='%s' style='background:%s;color:#fff;padding:10px 18px;"
                        "text-decoration:none;border-radius:4px;display:inline-block;"
                        "margin-right:8px'>%s</a>" % (url, colour, label))
        return (
            "<div style='font-family:Arial,sans-serif;font-size:14px;color:#222'>"
            "<h3 style='margin:0 0 6px'>%s</h3>"
            "<p style='margin:0 0 8px'>%s</p>%s%s%s"
            "<p style='color:#888;font-size:12px;margin-top:14px'>"
            "<a href='%s'>Open %s</a></p></div>"
        ) % (self.name, intro, table, buttons,
             ("<p style='color:#666;font-size:12px'>%s</p>" % footer) if footer else "",
             self._mail_url(), self.name)

    def _send_mail(self, ticket, partners, subject, body, attachment_ids=None):
        """Queue the mail; never break the workflow because of it."""
        try:
            return self.env["svcticket.channel"].send_mail(
                ticket, partners, subject, body, attachment_ids=attachment_ids)
        except Exception:                      # noqa: BLE001
            _logger.exception("mail failed: %s", subject)
            return self.env["mail.mail"]

    def _notify_mail(self, partners, subject, intro, rows=None, links=None,
                     attachment_ids=None, note=None):
        """Mail the partners and drop one short note in the chatter."""
        self.ensure_one()
        self._send_mail(self, partners, subject,
                        self._mail_html(intro, rows, links),
                        attachment_ids=attachment_ids)
        if note:
            self.sudo().message_post(body=note, subtype_xmlid="mail.mt_note",
                                     message_type="comment")
        return True

    # ============================================================ the events
    # 1. ticket raised -> only the team that got it
    def _mail_raised(self):
        self.ensure_one()
        team = self.team_id
        if not team:
            return False
        return self._notify_mail(
            self._recipients("team"),
            _("New ticket %s - %s") % (self.name, self.category_id.name),
            _("A new ticket was raised and it is yours to handle."),
            [(_("Ticket"), self.name),
             (_("Location"), self.location_id.name),
             (_("Category"), (self.subcategory_id or self.category_id).display_name),
             (_("What happened"), (self.description or "").replace("<p>", "").replace("</p>", "")[:180]),
             (_("Priority"), self.priority_id.name),
             (_("You must finish by"), self.sla_deadline),
             (_("Reported by"), self.reporter_id.name)])

    # 2. genuine check needed -> only the approvers configured on that category
    def _mail_genuine_check(self):
        self.ensure_one()
        return self._notify_mail(
            self._recipients("approvers"),
            _("Genuine check needed: %s") % self.name,
            _("A ticket in a category that needs a genuine check is waiting for you."),
            [(_("Ticket"), self.name),
             (_("Location"), self.location_id.name),
             (_("Category"), (self.subcategory_id or self.category_id).display_name),
             (_("What happened"), (self.description or "")[:180]),
             (_("Reported by"), self.reporter_id.name)],
            links=[(_("Open ticket"), self._mail_url(), "#0d6efd")])

    # 4. rejected at intake -> the reporter, with the reason only
    def _mail_intake_rejected(self, reason):
        self.ensure_one()
        return self._notify_mail(
            self._recipients("reporter"),
            _("Ticket %s was rejected") % self.name,
            _("Your ticket was not accepted."),
            [(_("Reason"), reason)])

    # 6 + 7. breach / escalation / unclaimed -> the team lead only
    def _mail_lead(self, subject, intro, rows=None):
        self.ensure_one()
        team = self.current_team_id or self.team_id
        return self._notify_mail(
            team.sudo().lead_ids.mapped("partner_id"), subject, intro, rows)

    # 8. hold requested -> the team lead only
    def _mail_hold_requested(self, is_po, note):
        self.ensure_one()
        return self._notify_mail(
            self._recipients("lead"),
            _("Hold approval needed: %s") % self.name,
            _("Someone in your team asked for a %s on this ticket. The team clock "
              "keeps running until you decide.") % (_("purchase order") if is_po else _("hold")),
            [(_("Ticket"), self.name),
             (_("Asked by"), self.env.user.name),
             (_("Reason"), self.hold_reason_id.name or "-"),
             (_("Justification"), note or "-"),
             (_("Estimated cost"), self.hold_cost),
             (_("Team deadline (unchanged)"), self.sla_deadline)],
            links=[(_("Open ticket"), self._mail_url(), "#0d6efd")])

    # 9 + 10. hold approved
    def _mail_hold_approved(self, needs_po):
        self.ensure_one()
        if needs_po:
            return self._notify_mail(
                self._recipients("po", "team", "reporter"),
                _("%s approved - now with the PO team") % self.name,
                _("The hold was approved. The ticket moved to the PO team, who can "
                  "now raise the purchase order."),
                [(_("Ticket"), self.name),
                 (_("Approved by"), self.env.user.name),
                 (_("With"), self.waiting_on),
                 (_("PO team must act by"), self.sla_deadline)])
        return self._notify_mail(
            self._recipients("team", "reporter"),
            _("%s is on hold") % self.name,
            _("The hold was approved. The ticket is parked and its clock is paused."),
            [(_("Ticket"), self.name),
             (_("Approved by"), self.env.user.name),
             (_("Reason"), self.hold_reason_id.name or "-")])

    # 11. hold rejected
    def _mail_hold_rejected(self, reason):
        self.ensure_one()
        team = self.current_team_id or self.team_id
        return self._notify_mail(
            (self.current_user_id.partner_id | team.sudo().member_ids.mapped("partner_id")
             | self.reporter_id.partner_id),
            _("%s hold rejected - carry on") % self.name,
            _("The hold request was rejected. Your clock was never stopped, so the "
              "original deadline still applies."),
            [(_("Ticket"), self.name),
             (_("Reason"), reason),
             (_("Your deadline"), self.sla_deadline)])

    # 12. PO sent for approval -> the approver(s) only
    def _mail_po_for_approval(self, po, approver, body, attachment_ids=None):
        self.ensure_one()
        return self._send_mail(
            self, approver.partner_id,
            _("Approval required: PO %s for %s") % (po.name, self.name),
            body, attachment_ids=attachment_ids)

    # 13 / 14 / 15 / 16. PO decided
    def _mail_po_decided(self, po, decided_by, approved, remarks=None, on_call=False):
        self.ensure_one()
        audience = self._recipients("po", "team", "lead", "reporter", "accounts")
        if approved:
            intro = _("PO <b>%s</b> was approved. The ticket moved to the accounts "
                      "team for payment.") % po.name
            subject = _("PO %s approved - %s") % (po.name, self.name)
        else:
            intro = _("<b>%s</b> rejected PO <b>%s</b>. The ticket went back to the "
                      "facility team to rework it.") % (decided_by.name, po.name)
            subject = _("PO %s rejected - %s") % (po.name, self.name)
        rows = [(_("Ticket"), self.name),
                (_("PO"), po.name),
                (_("Vendor"), po.vendor_name),
                (_("Amount"), "%.2f" % po.amount),
                (_("Decision"), _("Approved") if approved else _("Rejected")),
                (_("Decided by"), _("The director (on a call)") if on_call
                 else decided_by.name),
                (_("When"), fields.Datetime.now()),
                (_("Ticket now with"), self.waiting_on)]
        if on_call:
            rows.append((_("Recorded by"),
                         _("%s, on behalf of the director") % self.env.user.name))
        if remarks:
            rows.append((_("Remarks"), remarks))
        return self._notify_mail(audience, subject, intro, rows)

    # 16. a ticket just landed on the accounts desk
    def _mail_to_pay(self, po=None):
        self.ensure_one()
        return self._notify_mail(
            self._recipients("accounts"),
            _("Ticket %s needs payment") % self.name,
            _("An approved PO is ready to be paid."),
            [(_("Ticket"), self.name),
             (_("PO"), po.name if po else "-"),
             (_("Vendor"), po.vendor_name if po else "-"),
             (_("Amount to pay"), self.amount_due),
             (_("Accounts must act by"), self.sla_deadline)])

    # 17. added for payment
    def _mail_added_for_payment(self):
        self.ensure_one()
        return self._notify_mail(
            self._recipients("po", "team", "reporter"),
            _("Payment started for %s") % self.name,
            _("The accounts team picked the ticket up for payment."),
            [(_("Ticket"), self.name),
             (_("Amount"), self.amount_total),
             (_("Handled by"), self.env.user.name)])

    # 18. payment recorded
    def _mail_payment_recorded(self, amount, reference, tender):
        self.ensure_one()
        return self._notify_mail(
            self._recipients("team", "lead", "reporter", "po"),
            _("Payment recorded on %s") % self.name,
            _("A payment has been recorded."),
            [(_("Ticket"), self.name),
             (_("Amount paid now"), "%.2f" % amount),
             (_("Paid so far"), "%.2f" % self.amount_paid),
             (_("Balance left"), "%.2f" % self.amount_due),
             (_("Reference / UTR"), reference),
             (_("Tender"), tender),
             (_("Recorded by"), self.env.user.name)])

    # 20. invoice submitted -> accounts
    def _mail_invoice_submitted(self, invoice_number):
        self.ensure_one()
        return self._notify_mail(
            self._recipients("accounts"),
            _("Tax invoice submitted for %s") % self.name,
            _("A tax invoice was submitted for your acceptance."),
            [(_("Ticket"), self.name),
             (_("Invoice #"), invoice_number),
             (_("Amount due"), self.amount_due),
             (_("Accept or reject by"), self.sla_deadline)])

    # 21. invoice rejected -> only the team the invoice came from
    def _mail_invoice_rejected(self, reason):
        self.ensure_one()
        return self._notify_mail(
            self._team_people(self.team_id),
            _("Tax invoice rejected - %s") % self.name,
            _("The accounts team rejected the tax invoice."),
            [(_("Ticket"), self.name),
             (_("Reason"), reason),
             (_("Do next"), _("Upload a corrected invoice"))])

    # 22. invoice accepted -> only the team the invoice came from
    def _mail_invoice_accepted(self, invoice_number):
        self.ensure_one()
        return self._notify_mail(
            self._team_people(self.team_id),
            _("Tax invoice accepted - %s") % self.name,
            _("The invoice was accepted. You can close the ticket once the balance "
              "is settled."),
            [(_("Ticket"), self.name),
             (_("Invoice #"), invoice_number),
             (_("Balance left"), self.amount_due)])

    # 23 / 24. reporter documents
    def _mail_docs_requested(self):
        self.ensure_one()
        return self._notify_mail(
            self._recipients("reporter"),
            _("We need more from you: %s") % self.name,
            _("The team asked for documents or information before they can continue."),
            [(_("Ticket"), self.name),
             (_("Please answer by"), self.sla_deadline)])

    def _mail_docs_submitted(self):
        self.ensure_one()
        team = self.current_team_id or self.team_id
        return self._notify_mail(
            self._team_people(team),
            _("Reporter answered - %s") % self.name,
            _("The reporter uploaded what was asked for."),
            [(_("Ticket"), self.name),
             (_("Submitted by"), self.reporter_id.name)])

    # 27. reopened -> the team that has to pick it up again
    def _mail_reopened(self):
        self.ensure_one()
        team = self.current_team_id or self.team_id
        return self._notify_mail(
            self._team_people(team),
            _("Ticket reopened: %s") % self.name,
            _("A resolved ticket was reopened. It is back with your team."),
            [(_("Ticket"), self.name),
             (_("Location"), self.location_id.name),
             (_("Category"), (self.subcategory_id or self.category_id).display_name),
             (_("Reopened by"), self.env.user.name),
             (_("Your deadline"), self.sla_deadline)])

    # 25. resolved
    def _mail_resolved(self):
        self.ensure_one()
        return self._notify_mail(
            self._recipients("reporter"),
            _("Ticket %s resolved") % self.name,
            _("The issue was marked resolved. You can reopen it within 7 days if it "
              "comes back."),
            [(_("Ticket"), self.name),
             (_("Resolved by"), self.env.user.name)])
