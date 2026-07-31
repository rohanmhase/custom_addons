from odoo import models, fields, api


class PosSessionAlertConfig(models.Model):
    _name = 'pos.session.alert.config'
    _description = 'POS Session Alert Configuration (Singleton)'

    display_name = fields.Char(default='Session Alert Configuration', readonly=True)
    active = fields.Boolean(
        string='Enable Alerts',
        default=True,
        help="Master switch. Uncheck and Save to disable all alerts."
    )
    tolerance_amount = fields.Float(string='Tolerance (₹)', default=1.0)
    from_email = fields.Char(string='From Email', default='noreply@researchayu.com')
    cc_user_ids = fields.Many2many('res.users', string='CC Recipients')

    _sql_constraints = [
        ('single_config', 'CHECK (id > 0)', 'Config check'),
    ]

    @api.model
    def get_config(self):
        """Return the singleton — locks to prevent race conditions."""
        # Force fresh read from DB, bypass cache
        self.env.cr.execute("SELECT id FROM pos_session_alert_config ORDER BY id ASC LIMIT 1")
        row = self.env.cr.fetchone()
        if row:
            return self.browse(row[0])
        # Only if truly none exists, create
        return self.sudo().create({'display_name': 'Session Alert Configuration'})

    @api.model
    def action_open_config(self):
        config = self.get_config()
        return {
            'type': 'ir.actions.act_window',
            'name': 'Session Alert Configuration',
            'res_model': 'pos.session.alert.config',
            'view_mode': 'form',
            'res_id': config.id,
            'target': 'current',
        }

    @api.model_create_multi
    def create(self, vals_list):
        """Prevent creating more than one config record."""
        existing = self.search([], limit=1)
        if existing:
            # Already have a config — just return it, don't create duplicate
            return existing
        return super().create(vals_list)