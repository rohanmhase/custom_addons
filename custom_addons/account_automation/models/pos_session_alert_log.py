from odoo import models, fields


class PosSessionAlertLog(models.Model):
    _name = 'pos.session.alert.log'
    _description = 'POS Session Alert Log'
    _order = 'create_date desc'
    _rec_name = 'session_name'

    session_id = fields.Many2one('pos.session', string='Session', ondelete='set null')
    session_name = fields.Char(string='Session', required=True)
    clinic_id = fields.Many2one('pos.config', string='Clinic', ondelete='set null')
    clinic_name = fields.Char(string='Clinic', required=True)
    check_date = fields.Date(string='Business Day', required=True, index=True)
    alert_type = fields.Selection([
        ('opening_diff', 'Opening Difference'),
        ('closing_diff', 'Closing Difference'),
        ('not_closed', 'Session Not Closed'),
    ], string='Alert Type', required=True, index=True)
    diff_amount = fields.Float(string='Difference Amount')
    session_state_at_check = fields.Char(string='Session State')
    responsible_user_id = fields.Many2one('res.users', string='Responsible User')
    responsible_email = fields.Char(string='Notified Email')
    email_sent = fields.Boolean(string='Email Sent', default=False)
    email_error = fields.Text(string='Email Error')

    _sql_constraints = [
        ('uniq_alert_per_day',
         'UNIQUE(session_id, alert_type, check_date)',
         'Alert already exists for this session on this date.'),
    ]