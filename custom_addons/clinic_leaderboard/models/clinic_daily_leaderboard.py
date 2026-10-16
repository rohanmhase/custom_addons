"""Local Metabase snapshots for a lightweight Odoo 17 performance banner."""

import logging

import requests

from odoo import api, fields, models

_logger = logging.getLogger(__name__)


def _fetch_metabase_rows(url):
    """Fetch and validate *before* changing the database snapshot."""
    response = requests.get(url, timeout=30)
    response.raise_for_status()
    rows = response.json()
    if not isinstance(rows, list):
        raise ValueError("Metabase returned an unexpected response (expected a list)")
    return rows


def _valid_doctor_rows(rows):
    """Ignore malformed identifiers; normalize strings to integer IDs."""
    valid = []
    for row in rows:
        if not isinstance(row, dict):
            continue
        try:
            doctor_id = int(row.get('doctor_id') or 0)
        except (ValueError, TypeError):
            continue
        if doctor_id <= 0:
            continue
        valid.append({**row, 'doctor_id': doctor_id})
    return valid


# =========================================================================
# TABLE 1: Top 3 Performers (VAS, Therapies, Recovery)
# =========================================================================
class ClinicLeaderboardMetricsTopper(models.Model):
    _name = 'clinic.leaderboard.metrics.topper'
    _description = 'Metrics Toppers'

    doctor_id = fields.Integer(string="Doctor ID", index=True)
    doctor_name = fields.Char(string="Doctor Name")
    score = fields.Float(string="Score")
    category = fields.Char(string="Category")

    @api.model
    def _cron_update_metrics_toppers(self):
        url = "https://reports.researchayu.com/public/question/188cb47b-d1a4-48fe-bc47-d9c3e3eb8194.json"
        try:
            rows = _valid_doctor_rows(_fetch_metabase_rows(url))
            vals = [{
                'doctor_id': row.get('doctor_id'),
                'doctor_name': row.get('RS Name') or 'Unknown',
                'score': row.get('Score') or 0.0,
                'category': row.get('Category') or 'Unknown',
            } for row in rows]

            if not vals:
                _logger.warning("Metrics toppers: empty response, keeping last snapshot")
                return
            # If an import fails after unlink, savepoint restores the old rows.
            with self.env.cr.savepoint():
                self.sudo().search([]).unlink()
                self.sudo().create(vals)
        except Exception:
            _logger.exception("Metrics Toppers sync failed")


# =========================================================================
# TABLE 2: All Users (VAS, Therapies, Recovery)
# =========================================================================
class ClinicLeaderboardMetricsUser(models.Model):
    _name = 'clinic.leaderboard.metrics.user'
    _description = 'All Users Metrics'

    doctor_id = fields.Integer(string="Doctor ID", index=True)
    doctor_name = fields.Char(string="Doctor Name")

    total_therapies = fields.Integer(string="Total Therapies")
    prev_total_therapies = fields.Integer(string="Prev Total Therapies")

    recovery_percent = fields.Float(string="Recovery %")
    prev_recovery_percent = fields.Float(string="Prev Recovery %")

    vas_percent = fields.Float(string="VAS %")
    prev_total_vas = fields.Float(string="Prev VAS %")

    @api.model
    def get_dashboard_data(self):
        """Fetch the entire banner payload in one authenticated RPC.

        Department checks and group checks run once on the server, not in six
        serial browser requests. Doctor IDs are mapped as in the original
        module: the Metabase doctor_id must equal the Odoo user's ID.
        """
        empty = {
            'departmentName': '',
            'metricsUser': None,
            'extUser': None,
            'topMetrics': [],
            'topExt': [],
        }
        user = self.env.user
        if (not user.has_group('base.group_user') or
                user.has_group('clinic_leaderboard.group_hide_leaderboard')):
            return empty

        # Only examine the employee linked to the logged-in user. This does
        # not require giving ordinary employees general HR data access.
        employee = self.env['hr.employee'].sudo().search(
            [('user_id', '=', user.id)], limit=1,
        )
        if not employee or not employee.department_id:
            return empty

        department_name = employee.department_id.name or ''
        # The source module currently supports RS only, not BM/CS dashboards.
        if 'RS' not in department_name.upper():
            return empty

        my_records = [('doctor_id', '=', user.id)]
        metrics = self.search_read(
            my_records,
            ['recovery_percent', 'prev_recovery_percent', 'vas_percent',
             'prev_total_vas', 'total_therapies', 'prev_total_therapies'],
            limit=1,
        )
        extensions = self.env['clinic.leaderboard.extension.user'].search_read(
            my_records, ['extension_percent', 'prev_extension_percent'],
            limit=1,
        )
        # Normally 3 records per category; apply bounds in case of a faulty
        # import so the response does not grow without limit.
        metrics_toppers = self.env['clinic.leaderboard.metrics.topper'].search_read(
            [], ['doctor_name', 'score', 'category'], order='id', limit=60,
        )
        extension_toppers = self.env['clinic.leaderboard.extension.topper'].search_read(
            [], ['doctor_name', 'score', 'category'], order='id', limit=20,
        )
        return {
            'departmentName': department_name,
            'metricsUser': metrics[0] if metrics else None,
            'extUser': extensions[0] if extensions else None,
            'topMetrics': metrics_toppers,
            'topExt': extension_toppers,
        }

    @api.model
    def _cron_update_metrics_users(self):
        url = "https://reports.researchayu.com/public/question/f9f5b6a5-9757-405c-84f5-114c3ae5e30b.json"
        try:
            rows = _valid_doctor_rows(_fetch_metabase_rows(url))
            if not rows:
                _logger.warning("Metrics users: empty response, keeping last snapshot")
                return

            # Ignore duplicate external doctor IDs (last occurrence wins).
            rows = list({row['doctor_id']: row for row in rows}.values())
            # The cron can update the readonly-for-users snapshot. Fetching
            # existing rows once also avoids a DB lookup for each Metabase row.
            existing = {rec.doctor_id: rec for rec in self.sudo().search([])}
            new_rows = []
            with self.env.cr.savepoint():
                for row in rows:
                    doctor_id = row['doctor_id']

                    recovery = row.get('Total Recovery %') or 0.0
                    vas = row.get('VAS %') or 0.0
                    therapies = row.get('Total Therapies') or 0
                    name = row.get('RS Name') or 'Unknown'

                    if doctor_id in existing:
                        rec = existing.pop(doctor_id)
                        values = {
                            'doctor_name': name,
                            'prev_recovery_percent': rec.recovery_percent,
                            'recovery_percent': recovery,
                            'prev_total_vas': rec.vas_percent,
                            'vas_percent': vas,
                            'prev_total_therapies': rec.total_therapies,
                            'total_therapies': therapies,
                        }
                        # Do not write rows which are already up to date.
                        changes = {
                            key: value for key, value in values.items()
                            if getattr(rec, key) != value
                        }
                        if changes:
                            rec.write(changes)
                    else:
                        new_rows.append({
                            'doctor_id': doctor_id,
                            'doctor_name': name,
                            'recovery_percent': recovery,
                            'prev_recovery_percent': recovery,
                            'vas_percent': vas,
                            'prev_total_vas': vas,
                            'total_therapies': therapies,
                            'prev_total_therapies': therapies,
                        })

                if new_rows:
                    self.sudo().create(new_rows)
                if existing:
                    self.sudo().browse([rec.id for rec in existing.values()]).unlink()
        except Exception:
            _logger.exception("Metrics Users sync failed")


# =========================================================================
# TABLE 3: Top Performer (20-40% Extension)
# =========================================================================
class ClinicLeaderboardExtensionTopper(models.Model):
    _name = 'clinic.leaderboard.extension.topper'
    _description = 'Extension Topper'

    doctor_id = fields.Integer(string="Doctor ID", index=True)
    doctor_name = fields.Char(string="Doctor Name")
    score = fields.Float(string="Score")
    category = fields.Char(string="Category", default="20-40%")

    @api.model
    def _cron_update_extension_topper(self):
        url = "https://reports.researchayu.com/public/question/ccd4d2d0-f140-46e7-b340-de6aa5124b85.json"
        try:
            rows = _valid_doctor_rows(_fetch_metabase_rows(url))
            vals = [{
                'doctor_id': row.get('doctor_id'),
                'doctor_name': row.get('RS Name') or 'Unknown',
                'score': row.get('Score') or row.get('20-40%') or 0.0,
            } for row in rows]

            if not vals:
                _logger.warning("Extension toppers: empty response, keeping last snapshot")
                return
            with self.env.cr.savepoint():
                self.sudo().search([]).unlink()
                self.sudo().create(vals)
        except Exception:
            _logger.exception("Extension Toppers sync failed")


# =========================================================================
# TABLE 4: All Users (20-40% Extension)
# =========================================================================
class ClinicLeaderboardExtensionUser(models.Model):
    _name = 'clinic.leaderboard.extension.user'
    _description = 'All Users Extension'

    doctor_id = fields.Integer(string="Doctor ID", index=True)
    doctor_name = fields.Char(string="Doctor Name")
    extension_percent = fields.Float(string="20-40% Extension")
    prev_extension_percent = fields.Float(string="Prev 20-40% Extension")

    @api.model
    def _cron_update_extension_users(self):
        url = "https://reports.researchayu.com/public/question/b57115d8-1b47-42e9-b4c9-2c7ab96b486d.json"
        try:
            rows = _valid_doctor_rows(_fetch_metabase_rows(url))
            if not rows:
                _logger.warning("Extension users: empty response, keeping last snapshot")
                return

            rows = list({row['doctor_id']: row for row in rows}.values())
            existing = {rec.doctor_id: rec for rec in self.sudo().search([])}
            new_rows = []
            with self.env.cr.savepoint():
                for row in rows:
                    doctor_id = row['doctor_id']

                    percentage = row.get('20-40%') or 0.0
                    name = row.get('RS Name') or 'Unknown'
                    if doctor_id in existing:
                        rec = existing.pop(doctor_id)
                        values = {
                            'doctor_name': name,
                            'prev_extension_percent': rec.extension_percent,
                            'extension_percent': percentage,
                        }
                        changes = {
                            key: value for key, value in values.items()
                            if getattr(rec, key) != value
                        }
                        if changes:
                            rec.write(changes)
                    else:
                        new_rows.append({
                            'doctor_id': doctor_id,
                            'doctor_name': name,
                            'extension_percent': percentage,
                            'prev_extension_percent': percentage,
                        })

                if new_rows:
                    self.sudo().create(new_rows)
                if existing:
                    self.sudo().browse([rec.id for rec in existing.values()]).unlink()
        except Exception:
            _logger.exception("Extension Users sync failed")
