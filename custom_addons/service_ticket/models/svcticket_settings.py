# -*- coding: utf-8 -*-
"""Administration settings - one form, values kept in ir.config_parameter.

Lives under Configuration > Administration and is only reachable by users who
have Settings access rights (base.group_system), so it is not part of the
normal Settings app.
"""
from odoo import api, fields, models, _
from odoo.exceptions import UserError


class SvcTicketSettings(models.TransientModel):
    _name = "svcticket.settings"
    _description = "Service Ticket Administration"

    # ------------------------------------------------------- storage / S3
    s3_enabled = fields.Boolean(
        "Archive uploads to S3",
        help="Photos, payment proofs and invoices are pushed to the bucket and "
             "removed from the database. Off = normal Odoo attachments.")
    s3_bucket = fields.Char("Bucket")
    s3_region = fields.Char("Region")
    s3_key = fields.Char("Access Key")
    s3_secret = fields.Char("Secret Key")
    s3_prefix = fields.Char("Folder Prefix", default="service-tickets")

    # ------------------------------------------------------------- rules
    require_resolution_photo = fields.Boolean("Force a resolution photo", default=True)
    auto_close_days = fields.Integer("Auto-close resolved tickets after", default=7)

    # ================================================================ io
    @api.model
    def _defaults(self):
        get = self.env["ir.config_parameter"].sudo().get_param
        return {
            "s3_enabled": get("svcticket.s3_enabled", "0") in ("1", "True", "true"),
            "s3_bucket": get("svcticket.s3_bucket", False),
            "s3_region": get("svcticket.s3_region", "ap-south-1"),
            "s3_key": get("svcticket.s3_key", False),
            "s3_secret": get("svcticket.s3_secret", False),
            "s3_prefix": get("svcticket.s3_prefix", "service-tickets"),
            "require_resolution_photo":
                get("svcticket.require_resolution_photo", "1") in ("1", "True", "true"),
            "auto_close_days": int(get("svcticket.auto_close_days", 7) or 7),
        }

    @api.model
    def default_get(self, fields_list):
        values = super().default_get(fields_list)
        values.update({k: v for k, v in self._defaults().items() if k in fields_list})
        return values

    def action_open(self):
        """Menu entry point: always shows the current values."""
        return {
            "type": "ir.actions.act_window",
            "name": _("Administration"),
            "res_model": "svcticket.settings",
            "view_mode": "form",
            "target": "current",
            "res_id": self.create({}).id,
        }

    def action_save(self):
        self.ensure_one()
        if self.s3_enabled and not (self.s3_key and self.s3_secret and self.s3_bucket):
            raise UserError(_("S3 is switched on but the bucket, access key or secret "
                              "key is missing."))
        params = self.env["ir.config_parameter"].sudo()
        params.set_param("svcticket.s3_enabled", "1" if self.s3_enabled else "0")
        params.set_param("svcticket.s3_bucket", self.s3_bucket or "")
        params.set_param("svcticket.s3_region", self.s3_region or "ap-south-1")
        params.set_param("svcticket.s3_key", self.s3_key or "")
        params.set_param("svcticket.s3_secret", self.s3_secret or "")
        params.set_param("svcticket.s3_prefix", self.s3_prefix or "service-tickets")
        params.set_param("svcticket.require_resolution_photo",
                         "1" if self.require_resolution_photo else "0")
        params.set_param("svcticket.auto_close_days", self.auto_close_days or 7)
        return {
            "type": "ir.actions.client", "tag": "display_notification",
            "params": {"type": "success", "message": _("Settings saved."),
                       "sticky": False, "next": {"type": "ir.actions.act_window_close"}},
        }

    def action_test_s3(self):
        self.ensure_one()
        from .s3_storage import test_connection
        ok, message = test_connection(self.env)
        return {
            "type": "ir.actions.client", "tag": "display_notification",
            "params": {"type": "success" if ok else "danger", "message": message,
                       "sticky": False, "next": {"type": "ir.actions.act_window_close"}},
        }
