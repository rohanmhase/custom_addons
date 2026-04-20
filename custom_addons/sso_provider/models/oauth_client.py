from odoo import models, fields
import secrets
import datetime

class OAuthClient(models.Model):
    _name = 'oauth.client'
    _description = 'OAuth Client Application'

    name = fields.Char(string="Application Name", required=True, help="e.g. Moodle Grow")
    client_id = fields.Char(string="Client ID", required=True, readonly=True,
                            default=lambda self: secrets.token_hex(16))
    client_secret = fields.Char(string="Client Secret", required=True, readonly=True,
                                default=lambda self: secrets.token_hex(32))
    redirect_uri = fields.Char(string="Redirect URI", required=True,
                               help="The Moodle callback URL")

class OAuthCode(models.Model):
    _name = 'oauth.code'
    _description = 'Temporary Auth Code'

    code = fields.Char(default=lambda self: secrets.token_urlsafe(32))
    user_id = fields.Many2one('res.users', required=True)
    client_id = fields.Many2one('oauth.client', required=True)
    expires = fields.Datetime(default=lambda self: fields.Datetime.now() + datetime.timedelta(minutes=10))