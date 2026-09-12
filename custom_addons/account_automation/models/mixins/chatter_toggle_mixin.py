from odoo import models, fields


class ChatterToggleMixin(models.AbstractModel):
    _name = 'chatter.toggle.mixin'
    _description = 'Chatter Toggle Mixin'

    show_chatter = fields.Boolean(
        string='Show Activity Log',
        default=False
    )

    def action_toggle_chatter(self):
        for rec in self:
            rec.show_chatter = not rec.show_chatter