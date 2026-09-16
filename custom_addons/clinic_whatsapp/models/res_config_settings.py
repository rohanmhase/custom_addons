from odoo import models, fields

class ResConfigSettings(models.TransientModel):
    _inherit = "res.config.settings"

    whatsapp_api_url=fields.Char(
        string="Whatsapp API URL",
        config_parameter="clinic_whatsapp.api_url",
    )

    whatsapp_api_key= fields.Char(
        string="Whatsapp API Key",
        config_parameter="clinic_whatsapp.api_key",
    )