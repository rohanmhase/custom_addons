from odoo import models, fields

class ResUsers(models.Model):
    _inherit = "res.users"

    clinic_ids = fields.Many2many(
        'clinic.clinic',
        'clinic_user_rel',
        'user_id',
        'clinic_id',
        string="Clinics"
    )

    def write(self, vals):
        res = super().write(vals)

        if 'clinic_ids' in vals:
            # 🔥 CRITICAL FIX
            self.env['ir.rule'].clear_caches()
            self.clear_caches()

        return res