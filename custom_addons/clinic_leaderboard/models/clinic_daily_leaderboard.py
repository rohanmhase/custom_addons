import requests
import logging
from odoo import models, fields, api

_logger = logging.getLogger(__name__)


# =========================================================================
# TABLE 1: Top 3 Performers (VAS, Therapies, Recovery)
# =========================================================================
class ClinicLeaderboardMetricsTopper(models.Model):
    _name = 'clinic.leaderboard.metrics.topper'
    _description = 'Metrics Toppers'

    doctor_id = fields.Integer(string="Doctor ID")
    doctor_name = fields.Char(string="Doctor Name")
    score = fields.Float(string="Score")
    category = fields.Char(string="Category")

    @api.model
    def _cron_update_metrics_toppers(self):
        url = "https://reports.researchayu.com/public/question/188cb47b-d1a4-48fe-bc47-d9c3e3eb8194.json"
        try:
            results = requests.get(url, timeout=30).json()
            self.search([]).unlink()

            vals = [{
                'doctor_id': r.get('doctor_id'),
                'doctor_name': r.get('RS Name') or 'Unknown',
                'score': r.get('Score') or 0.0,
                'category': r.get('Category') or 'Unknown'
            } for r in results if r.get('doctor_id')]

            if vals: self.create(vals)
        except Exception as e:
            _logger.error(f"Metrics Toppers failed: {e}")


# =========================================================================
# TABLE 2: All Users (VAS, Therapies, Recovery)
# =========================================================================
class ClinicLeaderboardMetricsUser(models.Model):
    _name = 'clinic.leaderboard.metrics.user'
    _description = 'All Users Metrics'

    doctor_id = fields.Integer(string="Doctor ID")
    doctor_name = fields.Char(string="Doctor Name")
    total_therapies = fields.Integer(string="Total Therapies")
    recovery_percent = fields.Float(string="Recovery %")
    prev_recovery_percent = fields.Float(string="Prev Recovery %")
    vas_percent = fields.Float(string="VAS %")

    @api.model
    def _cron_update_metrics_users(self):
        url = "https://reports.researchayu.com/public/question/f9f5b6a5-9757-405c-84f5-114c3ae5e30b.json"
        try:
            results = requests.get(url, timeout=30).json()
            existing = {rec.doctor_id: rec for rec in self.search([])}

            vals_list = []
            for r in results:
                d_id = r.get('doctor_id')
                if not d_id: continue

                new_rec = r.get('Total Recovery %') or 0.0
                if d_id in existing:
                    rec = existing.pop(d_id)
                    rec.write({
                        'prev_recovery_percent': rec.recovery_percent,
                        'recovery_percent': new_rec,
                        'vas_percent': r.get('VAS %') or 0.0,
                        'total_therapies': r.get('Total Therapies') or 0
                    })
                else:
                    vals_list.append({
                        'doctor_id': d_id,
                        'doctor_name': r.get('RS Name') or 'Unknown',
                        'recovery_percent': new_rec,
                        'prev_recovery_percent': new_rec,
                        'vas_percent': r.get('VAS %') or 0.0,
                        'total_therapies': r.get('Total Therapies') or 0
                    })

            if vals_list: self.create(vals_list)
            if existing: self.browse([rec.id for rec in existing.values()]).unlink()
        except Exception as e:
            _logger.error(f"Metrics Users failed: {e}")


# =========================================================================
# TABLE 3: Top Performer (20-40% Extension)
# =========================================================================
class ClinicLeaderboardExtensionTopper(models.Model):
    _name = 'clinic.leaderboard.extension.topper'
    _description = 'Extension Topper'

    doctor_id = fields.Integer(string="Doctor ID")
    doctor_name = fields.Char(string="Doctor Name")
    score = fields.Float(string="Score")
    category = fields.Char(string="Category", default="20-40%")

    @api.model
    def _cron_update_extension_topper(self):
        url = "https://reports.researchayu.com/public/question/ccd4d2d0-f140-46e7-b340-de6aa5124b85.json"
        try:
            results = requests.get(url, timeout=30).json()
            self.search([]).unlink()

            vals = [{
                'doctor_id': r.get('doctor_id'),
                'doctor_name': r.get('RS Name') or 'Unknown',
                'score': r.get('Score') or r.get('20-40%') or 0.0,
            } for r in results if r.get('doctor_id')]

            if vals: self.create(vals)
        except Exception as e:
            _logger.error(f"Extension Topper failed: {e}")


# =========================================================================
# TABLE 4: All Users (20-40% Extension)
# =========================================================================
class ClinicLeaderboardExtensionUser(models.Model):
    _name = 'clinic.leaderboard.extension.user'
    _description = 'All Users Extension'

    doctor_id = fields.Integer(string="Doctor ID")
    doctor_name = fields.Char(string="Doctor Name")
    extension_percent = fields.Float(string="20-40% Extension")
    prev_extension_percent = fields.Float(string="Prev 20-40% Extension")

    @api.model
    def _cron_update_extension_users(self):
        url = "https://reports.researchayu.com/public/question/b57115d8-1b47-42e9-b4c9-2c7ab96b486d.json"
        try:
            results = requests.get(url, timeout=30).json()
            existing = {rec.doctor_id: rec for rec in self.search([])}

            vals_list = []
            for r in results:
                d_id = r.get('doctor_id')
                if not d_id: continue

                new_ext = r.get('20-40%') or 0.0
                if d_id in existing:
                    rec = existing.pop(d_id)
                    rec.write({
                        'prev_extension_percent': rec.extension_percent,
                        'extension_percent': new_ext,
                    })
                else:
                    vals_list.append({
                        'doctor_id': d_id,
                        'doctor_name': r.get('RS Name') or 'Unknown',
                        'extension_percent': new_ext,
                        'prev_extension_percent': new_ext,
                    })

            if vals_list: self.create(vals_list)
            if existing: self.browse([rec.id for rec in existing.values()]).unlink()
        except Exception as e:
            _logger.error(f"Extension Users failed: {e}")