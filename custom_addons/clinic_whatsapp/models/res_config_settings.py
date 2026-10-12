from odoo import fields, models


class ResConfigSettings(models.TransientModel):
  _inherit = 'res.config.settings'

  whatsapp_api_url = fields.Char(
      string='Meta Graph API Base URL',
      default='https://graph.facebook.com/v25.0',
      config_parameter='clinic_whatsapp.api_url',
  )
  whatsapp_api_key = fields.Char(
      string='Permanent System User Token',
      config_parameter='clinic_whatsapp.api_key',
  )
  whatsapp_default_phone_number_id = fields.Char(
      string='Default Sender Phone Number ID',
      default='1395062320356609',
      config_parameter='clinic_whatsapp.phone_number_id',
  )
  whatsapp_waba_id = fields.Char(
      string='WhatsApp Business Account (WABA) ID',
      default='1456832022990876',
      config_parameter='clinic_whatsapp.waba_id',
  )
  whatsapp_public_base_url = fields.Char(
      string='Public Web Base URL',
      config_parameter='clinic_whatsapp.public_base_url',
  )
  whatsapp_verify_token = fields.Char(
      string='Webhook Verify Token',
      default='clinic_wa_verify_2026',
      config_parameter='clinic_whatsapp.verify_token',
      help=(
          'Match this token inside Meta Developer Console > WhatsApp >'
          ' Configuration > Webhook'
      ),
  )