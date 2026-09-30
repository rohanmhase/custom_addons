import requests
import logging
from odoo import models, fields, api

_logger = logging.getLogger(__name__)


class ClinicDailyLeaderboard(models.Model):
    _name = 'clinic.daily.leaderboard'
    _description = 'Daily Clinic Leaderboard Cache'

    doctor_id = fields.Many2one('res.users', string="Doctor User")
    doctor_name = fields.Char(string="Therapy Doctor")
    clinics_handled = fields.Char(string="Clinics Handled")
    total_pt = fields.Integer(string="Total PT")
    eligible_pt = fields.Integer(string="Eligible PT")
    recovery_percent = fields.Float(string="Recovery %")
    vas_percent = fields.Float(string="VAS %")

    @api.model
    def _cron_update_leaderboard(self):
        """ Fetch data from Metabase public JSON link and rebuild the leaderboard """
        metabase_json_url = "https://reports.researchayu.com/public/question/f9f5b6a5-9757-405c-84f5-114c3ae5e30b.json"

        try:
            response = requests.get(metabase_json_url, timeout=30)
            response.raise_for_status()
            metabase_results = response.json()

            # Clear existing data only if the fetch was successful
            self.search([]).unlink()

            # 1. Extract all raw doctor IDs from the Metabase payload
            raw_doctor_ids = [row.get('doctor_id') for row in metabase_results if row.get('doctor_id')]

            # 2. Query Odoo to find which of these IDs actually exist in res_users
            existing_users = self.env['res.users'].search([('id', 'in', raw_doctor_ids)])
            valid_user_ids = set(existing_users.mapped('id'))

            vals_list = []
            for index, row in enumerate(metabase_results):
                raw_id = row.get('doctor_id')

                # 3. If the ID is not in Odoo, assign False to avoid the Foreign Key Violation
                safe_doctor_id = raw_id if raw_id in valid_user_ids else False

                vals_list.append({
                    'doctor_id': safe_doctor_id,
                    'doctor_name': row.get('Therapy Doctor') or 'Unknown',
                    'clinics_handled': row.get('Clinics Handled') or '',
                    'total_pt': row.get('Total PT Handled') or 0,
                    'eligible_pt': row.get('Eligible PT') or 0,
                    'recovery_percent': row.get('Overall Recovery %') or 0.0,
                    'vas_percent': row.get('VAS %') or 0.0,
                })

            if vals_list:
                self.create(vals_list)

        except requests.exceptions.RequestException as e:
            _logger.error(f"Failed to fetch leaderboard data from Metabase: {e}")