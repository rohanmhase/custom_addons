from odoo import fields, models, api, _

class ConsentForm(models.Model):
    _name = 'consent.form'
    _description = 'Consent Form'

    company_id = fields.Many2one('res.company', string='Company', default=lambda self: self.env.company)
    # created_by_name = fields.Char(related='create_uid.name', string='Created By')
    patient_id = fields.Many2one('clinic.patient', string='Patient')
    patient_name = fields.Char(related='patient_id.name', string='Patient Name')
    patient_mrn = fields.Char(related='patient_id.mrn', string='MRN No.')

    consent_language = fields.Selection([
        ('en_US', 'English'),
        ('hi_IN', 'Hindi'),
        ('mr_IN', 'Marathi'),
        ('kn_IN', 'Kannada'),
        ('gu_IN', 'Gujarati'),
        ('te_IN', 'Telugu'),
    ], string='Language', default='en_US')

    def action_download_consultation(self):
        return self.env.ref('patient_management.action_report_consultation_consent').with_context(
            lang=self.consent_language,
            report_type='consultation'  # This is used by the XML print_report_name
        ).report_action(self)

    def action_download_treatment(self):
        return self.env.ref('patient_management.action_report_treatment_consent').with_context(
            lang=self.consent_language,
            report_type='treatment'  # This is used by the XML print_report_name
        ).report_action(self)