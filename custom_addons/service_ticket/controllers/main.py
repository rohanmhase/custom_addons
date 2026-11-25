# -*- coding: utf-8 -*-
"""Approve / reject a PO straight from the email link - no login needed.

The token is single-use, tied to one PO, and expires with the approval clock.
"""
import logging

from odoo import http
from odoo.http import request

_logger = logging.getLogger(__name__)

PAGE = """<!DOCTYPE html><html><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>__TITLE__</title>
<style>
 body{margin:0;font-family:-apple-system,Segoe UI,Roboto,Arial,sans-serif;
      background:#f4f6f9;display:flex;min-height:100vh;align-items:center;justify-content:center}
 .card{background:#fff;border-radius:10px;box-shadow:0 4px 24px rgba(0,0,0,.08);
       padding:28px;max-width:460px;width:92%}
 h2{margin:0 0 6px;font-size:20px;color:#111}
 p{color:#555;font-size:14px;line-height:1.5}
 .btn{display:inline-block;padding:11px 22px;border-radius:6px;border:none;
      font-size:15px;cursor:pointer;text-decoration:none;color:#fff}
 .ok{background:#198754}.no{background:#dc3545}.muted{background:#6c757d}
 textarea{width:100%;box-sizing:border-box;min-height:100px;padding:10px;
          border:1px solid #ced4da;border-radius:6px;font-size:14px;margin-top:8px}
 label{font-size:13px;font-weight:600;color:#333}
</style></head><body><div class="card">__BODY__</div></body></html>"""


def _page(title, body_html):
    return PAGE.replace("__TITLE__", title).replace("__BODY__", body_html)


class ApprovalController(http.Controller):

    @http.route("/svcticket/approve/<string:token>", type="http", auth="public",
                methods=["GET", "POST"], csrf=False)
    def approve(self, token, **kw):
        record = request.env["svcticket.approval.token"].sudo()._find(token)
        if not record:
            return request.make_response(_page("Invalid link", "<h2>Link not found</h2>"
                                                          "<p>Ask the PO team to resend it.</p>"),
                                         headers=[("Content-Type", "text/html")])
        if not record._valid():
            return request.make_response(_page(
                "Link expired",
                "<h2>Link expired</h2><p>This approval link was already used or has "
                "expired. Please open <b>Service Ticket &rarr; My Approvals</b>.</p>"),
                headers=[("Content-Type", "text/html")])

        po = record.po_id
        if request.httprequest.method == "POST":
            record._consume()
            po.action_approve(user=record.user_id)
            return request.make_response(_page(
                "Approved", "<h2>Approved</h2><p>PO %s approved. The payment team has been "
                            "notified.</p>" % po.name),
                headers=[("Content-Type", "text/html")])

        body = ("<h2>Approve this PO?</h2>"
                "<p><b>%s</b> &middot; %s<br/>Amount: %s %.2f</p>"
                "<form method='post'>"
                "<button class='btn ok' type='submit'>Approve</button>&nbsp;"
                "<a class='btn no' href='/svcticket/reject/%s'>Reject</a></form>"
                % (po.name, po.ticket_id.name, po.currency_id.symbol, po.amount, token))
        return request.make_response(_page("Approve PO", body),
                                     headers=[("Content-Type", "text/html")])

    @http.route("/svcticket/reject/<string:token>", type="http", auth="public",
                methods=["GET", "POST"], csrf=False)
    def reject(self, token, **kw):
        record = request.env["svcticket.approval.token"].sudo()._find(token)
        if not record or not record._valid():
            return request.make_response(_page(
                "Link expired", "<h2>Link expired</h2><p>Open <b>My Approvals</b> instead.</p>"),
                headers=[("Content-Type", "text/html")])

        po = record.po_id
        if request.httprequest.method == "POST":
            remarks = (kw.get("remarks") or "").strip()
            if not remarks:
                return request.make_response(_page(
                    "Remarks required",
                    "<h2>Remarks required</h2><p>A rejection reason is mandatory.</p>"
                    "<a class='btn muted' href='/svcticket/reject/%s'>Back</a>" % token),
                    headers=[("Content-Type", "text/html")])
            record._consume()
            po.action_reject(remarks, user=record.user_id)
            return request.make_response(_page(
                "Rejected", "<h2>Rejected</h2><p>PO %s rejected and the facility team "
                            "has been informed.</p>" % po.name),
                headers=[("Content-Type", "text/html")])

        body = ("<h2>Reject this PO</h2><p><b>%s</b> &middot; %s</p>"
                "<form method='post'><label>Reason (required)</label>"
                "<textarea name='remarks' required></textarea>"
                "<p><button class='btn no' type='submit'>Submit rejection</button></p></form>"
                % (po.name, po.ticket_id.name))
        return request.make_response(_page("Reject PO", body),
                                     headers=[("Content-Type", "text/html")])
