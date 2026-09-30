import requests
import logging
from odoo import models, fields, api

_logger = logging.getLogger(__name__)

class ClinicRSLeaderboard(models.Model):
    _name = 'clinic.rs.leaderboard'
    _description = 'RS Therapy Metrics Leaderboard'
    # No _order defined here since we removed the rank field

    doctor_id = fields.Many2one('res.users', string="Doctor User")
    doctor_name = fields.Char(string="Doctor Name")

    # NEW: Added Clinics Handled field
    clinics_handled = fields.Char(string="Clinics Handled")

    total_therapies = fields.Integer(string="Total Therapies")
    ext_20_40_percent = fields.Float(string="20-40 Ext %")

    @api.model
    def _cron_update_rs_leaderboard(self):
        # Remember to use your actual UUID link here
        metabase_json_url = "https://reports.researchayu.com/public/question/ccd4d2d0-f140-46e7-b340-de6aa5124b85.json"

        try:
            response = requests.get(metabase_json_url, timeout=30)
            response.raise_for_status()
            metabase_results = response.json()

            self.search([]).unlink()

            raw_doctor_ids = [row.get('doctor_id') for row in metabase_results if row.get('doctor_id')]
            existing_users = self.env['res.users'].search([('id', 'in', raw_doctor_ids)])
            valid_user_ids = set(existing_users.mapped('id'))

            vals_list = []
            for row in metabase_results:
                raw_id = row.get('doctor_id')
                safe_doctor_id = raw_id if raw_id in valid_user_ids else False

                vals_list.append({
                    'doctor_id': safe_doctor_id,
                    'doctor_name': row.get('RS Name') or 'Unknown',

                    # NEW: Mapping from Metabase to Odoo
                    'clinics_handled': row.get('Clinics Handled') or '',

                    'total_therapies': row.get('Total Patients / Therapies') or 0,
                    'ext_20_40_percent': row.get('20-40%') or 0.0,
                })

            if vals_list:
                self.create(vals_list)

        except requests.exceptions.RequestException as e:
            _logger.error(f"Failed to fetch RS leaderboard data from Metabase: {e}")