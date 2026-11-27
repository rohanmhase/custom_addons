"""Local Metabase snapshots for a lightweight Odoo 17 performance banner."""

import logging
from math import isfinite
from datetime import date

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



# The four *.user models are durable records, not replaceable weekly snapshots.
# Import ONLY doctors present in the current Metabase response. A missing doctor
# is never modified, deactivated or deleted. The topper models below deliberately
# retain their separate rotating-snapshot behavior.
def _upsert_leaderboard_users(model, rows, *, name_column, columns):
    model = model.sudo()

    # If Metabase unexpectedly returns an ID more than once, retain the last
    # response row, as before, and log the issue rather than creating duplicates.
    incoming = {}
    for row in rows:
        doctor_id = row['doctor_id']
        if doctor_id in incoming:
            _logger.warning("%s: duplicate Metabase doctor_id %s; last row used",
                            model._name, doctor_id)
        incoming[doctor_id] = row

    # Keep existing duplicate Odoo records too. All records matching an incoming
    # doctor_id are updated, and none are deleted or merged by the importer.
    by_doctor = {}
    for record in model.search([]):
        by_doctor.setdefault(record.doctor_id, []).append(record)

    new_rows = []
    updated = 0
    with model.env.cr.savepoint():
        for doctor_id, row in incoming.items():
            name = row.get(name_column)
            name = str(name).strip() if name is not None else ''
            current_values = {}

            for labels, current_field, _previous_field in columns:
                labels = (labels,) if isinstance(labels, str) else labels
                for label in labels:
                    if label not in row:
                        continue
                    raw = row[label]
                    # Missing / null fields must not reset existing values.
                    if raw is not None:
                        try:
                            value = float(raw)
                            if not isfinite(value):
                                raise ValueError("non-finite number")
                            field_type = model._fields[current_field].type
                            if field_type == 'integer':
                                if not value.is_integer():
                                    raise ValueError("non-integral value for integer field")
                                value = int(value)
                            current_values[current_field] = value
                        except (TypeError, ValueError):
                            _logger.warning("%s: skip invalid %s for doctor_id %s: %r",
                                            model._name, label, doctor_id, raw)
                    break

            matching = by_doctor.get(doctor_id)
            if matching:
                for record in matching:
                    changes = {}
                    if name and record.doctor_name != name:
                        changes['doctor_name'] = name
                    for _labels, current_field, previous_field in columns:
                        if current_field not in current_values:
                            continue
                        new_value = current_values[current_field]
                        old_value = getattr(record, current_field)
                        if old_value != new_value:
                            # Shift the old value only when this metric changes;
                            # repeat runs with identical data are idempotent.
                            changes[previous_field] = old_value
                            changes[current_field] = new_value
                    if changes:
                        record.write(changes)
                        updated += 1
            else:
                vals = {'doctor_id': doctor_id, 'doctor_name': name or 'Unknown'}
                for _labels, current_field, previous_field in columns:
                    if current_field in current_values:
                        vals[current_field] = current_values[current_field]
                        vals[previous_field] = current_values[current_field]
                new_rows.append(vals)

        if new_rows:
            model.create(new_rows)

    untouched = sum(len(records) for doctor_id, records in by_doctor.items()
                    if doctor_id not in incoming)
    _logger.info("%s Metabase sync: incoming=%d, updated=%d, created=%d, "
                 "unmatched records left untouched=%d", model._name,
                 len(incoming), updated, len(new_rows), untouched)


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
        """Return RS or CS leaderboard data in a single, non-blocking RPC.

        The Odoo browser caller is restricted to an internal user and an
        employee of either department. CS data is read with sudo because this
        one-file change cannot add a new ir.model.access.csv entry.
        """
        empty = {
            'departmentName': '', 'metricsUser': None,
            'extUser': None, 'topMetrics': [], 'topExt': [],
        }
        user = self.env.user
        if (not user.has_group('base.group_user') or
                user.has_group('clinic_leaderboard.group_hide_leaderboard')):
            return empty

        # Consider multiple employees and CS job titles, as the Metabase SQL
        # qualifies CS by department OR job_title.
        employees = self.env['hr.employee'].sudo().search([
            ('user_id', '=', user.id), ('active', '=', True),
        ])
        if not employees:
            return empty

        def is_cs(emp):
            department = emp.department_id.name if emp.department_id else ''
            return ('CS' in (department or '').upper() or
                    'CS' in (emp.job_title or '').upper())

        if any(is_cs(emp) for emp in employees):
            my_records = [('doctor_id', '=', user.id)]

            # Fetch personal metrics for the logged-in CS user
            cs_metrics = self.env['clinic.leaderboard.cs.user'].sudo().search_read(
                my_records,
                ['ppc_amount', 'prev_ppc_amount', 'conversion_percent',
                 'prev_conversion_percent', 'only_consultation',
                 'prev_only_consultation', 'same_day_conversion',
                 'prev_same_day_conversion'],
                limit=1,
            )

            # Bounded 4-row sudo read for toppers
            cs_toppers = self.env['clinic.leaderboard.cs.topper'].sudo().search_read(
                [], ['doctor_name', 'score', 'category'],
                order='id', limit=4,
            )

            return {
                **empty,
                'departmentName': 'CS',
                'metricsUser': cs_metrics[0] if cs_metrics else None,  # Passes personal CS data to frontend
                'topMetrics': [{
                    'doctor_name': topper['doctor_name'],
                    'score': topper['score'],
                    'category': topper['category'],
                } for topper in cs_toppers],
            }

        def is_bm(emp):
            department = emp.department_id.name if emp.department_id else ''
            return ('BM' in (department or '').upper() or
                    'BM' in (emp.job_title or '').upper())

        # Add this block to route BM Data
        if any(is_bm(emp) for emp in employees):
            my_records = [('doctor_id', '=', user.id)]
            bm_metrics = self.env['clinic.leaderboard.bm.user'].sudo().search_read(
                my_records,
                ['ppc_amount', 'prev_ppc_amount', 'conversion_percent',
                 'prev_conversion_percent', 'npc', 'prev_npc',
                 'referral_visits', 'prev_referral_visits',
                 'pc_p_joined', 'prev_pc_p_joined'],
                limit=1,
            )

            bm_toppers = self.env['clinic.leaderboard.bm.topper'].sudo().search_read(
                [], ['doctor_name', 'score', 'category'],
                order='id', limit=5,
            )

            return {
                **empty,
                'departmentName': 'BM',
                'metricsUser': bm_metrics[0] if bm_metrics else None,
                'topMetrics': [{
                    'doctor_name': topper['doctor_name'],
                    'score': topper['score'],
                    'category': topper['category'],
                } for topper in bm_toppers],
            }

        rs_employee = next((emp for emp in employees if emp.department_id
                            and 'RS' in (emp.department_id.name or '').upper()), None)
        if not rs_employee:
            return empty

        department_name = rs_employee.department_id.name or ''
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
        if date.today().day not in (1, 16):
            return

        url = "https://reports.researchayu.com/public/question/f9f5b6a5-9757-405c-84f5-114c3ae5e30b.json"
        try:
            rows = _valid_doctor_rows(_fetch_metabase_rows(url))
            if not rows:
                _logger.warning("Metrics users: empty response; existing records unchanged")
                return
            _upsert_leaderboard_users(
                self, rows, name_column='RS Name',
                columns=(
                    ('Total Recovery %', 'recovery_percent', 'prev_recovery_percent'),
                    ('VAS %', 'vas_percent', 'prev_total_vas'),
                    ('Total Therapies', 'total_therapies', 'prev_total_therapies'),
                ),
            )
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
                'score': row.get('Score') or 0.0,
                'category': row.get('Category') or 'Ext. 20-40%',
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
        if date.today().day not in (1, 16):
            return

        url = "https://reports.researchayu.com/public/question/b57115d8-1b47-42e9-b4c9-2c7ab96b486d.json"
        try:
            rows = _valid_doctor_rows(_fetch_metabase_rows(url))
            if not rows:
                _logger.warning("Extension users: empty response; existing records unchanged")
                return
            _upsert_leaderboard_users(
                self, rows, name_column='RS Name',
                columns=(('20-40%', 'extension_percent', 'prev_extension_percent'),),
            )
        except Exception:
            _logger.exception("Extension Users sync failed")


# =========================================================================
# TABLE 5: Top Performers (CS Department: previous Mon-Sun week)
# =========================================================================
class ClinicLeaderboardCSTopper(models.Model):
    _name = 'clinic.leaderboard.cs.topper'
    _description = 'CS Toppers'

    doctor_id = fields.Integer(string="Doctor ID", index=True)
    doctor_name = fields.Char(string="Doctor Name")
    score = fields.Float(string="Score")
    category = fields.Char(string="Category")

    @api.model
    def _cron_update_cs_toppers(self):
        url = "https://reports.researchayu.com/public/question/a9f05cef-f6b3-4311-9f9e-67a7041cacdb.json"
        try:
            rows = _valid_doctor_rows(_fetch_metabase_rows(url))
            print(rows)
            vals = [{
                'doctor_id': row.get('doctor_id'),
                'doctor_name': row.get('cs_name') or 'Unknown',
                'score': row.get('metric_value') or 0.0,
                'category': row.get('category') or 'Unknown',
            } for row in rows]

            if not vals:
                _logger.warning("CS toppers: empty response, keeping last snapshot")
                return
            # If an import fails after unlink, savepoint restores the old rows.
            with self.env.cr.savepoint():
                self.sudo().search([]).unlink()
                self.sudo().create(vals)
        except Exception:
            _logger.exception("CS Toppers sync failed")


# =========================================================================
# TABLE 6: All Users (CS Department: previous Mon-Sun week)
# =========================================================================

class ClinicLeaderboardCSUser(models.Model):
    _name = 'clinic.leaderboard.cs.user'
    _description = 'All CS Users Performance'

    doctor_id = fields.Integer(string="Doctor ID", index=True)
    doctor_name = fields.Char(string="Doctor Name")  # FIXED: Changed from cs_name to doctor_name
    ppc_amount = fields.Float(string="PPC Amount")
    conversion_percent = fields.Float(string="Conversion Percent")
    only_consultation = fields.Integer(string="Only Consultation")
    same_day_conversion = fields.Integer(string="Same Day Conversion")
    prev_ppc_amount = fields.Float(string="Prev PPC Amount")
    prev_conversion_percent = fields.Float(string="Prev Conversion Percent")
    prev_only_consultation = fields.Integer(string="Prev Only Consultation")
    prev_same_day_conversion = fields.Integer(string="Prev Same Day Conversion")

    @api.model
    def _cron_update_cs_metrics_users(self):
        url = "https://reports.researchayu.com/public/question/5147bd94-999c-4dbf-9988-d650fca90690.json"
        try:
            rows = _valid_doctor_rows(_fetch_metabase_rows(url))
            if not rows:
                _logger.warning("CS users: empty response; existing records unchanged")
                return
            _upsert_leaderboard_users(
                self, rows, name_column='CS Name',
                columns=(
                    ('PPC Amount', 'ppc_amount', 'prev_ppc_amount'),
                    ('Conversion %', 'conversion_percent', 'prev_conversion_percent'),
                    ('Only Consultation', 'only_consultation', 'prev_only_consultation'),
                    ('Same Day Conversion', 'same_day_conversion', 'prev_same_day_conversion'),
                ),
            )
        except Exception:
            _logger.exception("CS Users sync failed")

# =========================================================================
# TABLE 7: Top Performers (BM Department: previous Mon-Sun week)
# =========================================================================
class ClinicLeaderboardBMTopper(models.Model):
    _name = 'clinic.leaderboard.bm.topper'
    _description = 'BM Toppers'

    doctor_id = fields.Integer(string="Doctor ID", index=True)
    doctor_name = fields.Char(string="Doctor Name")
    score = fields.Float(string="Score")
    category = fields.Char(string="Category")

    @api.model
    def _cron_update_bm_toppers(self):
        url = "https://reports.researchayu.com/public/question/f89a61fb-2404-4be7-ae20-0104fdef54c2.json"
        try:
            rows = _valid_doctor_rows(_fetch_metabase_rows(url))

            vals = [{
                'doctor_id': row.get('doctor_id'),
                'doctor_name': row.get('bm_name') or 'Unknown',
                'score': row.get('metric_value') or 0.0,
                'category': row.get('category') or 'Unknown',
            } for row in rows]

            if not vals:
                _logger.warning("BM toppers: empty response, keeping last snapshot")
                return
            # If an import fails after unlink, savepoint restores the old rows.
            with self.env.cr.savepoint():
                self.sudo().search([]).unlink()
                self.sudo().create(vals)
        except Exception:
            _logger.exception("BM Toppers sync failed")


# =========================================================================
# TABLE 8: All Users (BM Department: previous Mon-Sun week)
# =========================================================================

class ClinicLeaderboardBMUser(models.Model):
    _name = 'clinic.leaderboard.bm.user'
    _description = 'All BM Users Performance'

    doctor_id = fields.Integer(string="Doctor ID", index=True)
    doctor_name = fields.Char(string="Doctor Name")  # FIXED: Changed from cs_name to doctor_name
    ppc_amount = fields.Float(string="PPC Amount")
    conversion_percent = fields.Float(string="Conversion Percent")
    npc = fields.Float(string="NPC")
    referral_visits = fields.Integer(string="Referral Visits")
    pc_p_joined = fields.Integer(string="PC+P1 Joined")
    prev_ppc_amount = fields.Float(string="Prev PPC Amount")
    prev_conversion_percent = fields.Float(string="Prev Conversion Percent")
    prev_npc = fields.Float(string="Prev NPC")
    prev_referral_visits = fields.Integer(string="Prev Referral Visits")
    prev_pc_p_joined = fields.Integer(string="Prev PC+P1 Joined")

    @api.model
    def _cron_update_bm_metrics_users(self):
        url = "https://reports.researchayu.com/public/question/4025cfc4-2d7b-434d-80fa-e515c63cee79.json"
        try:
            rows = _valid_doctor_rows(_fetch_metabase_rows(url))
            if not rows:
                _logger.warning("BM users: empty response; existing records unchanged")
                return
            _upsert_leaderboard_users(
                self, rows, name_column='BM Name',
                columns=(
                    ('PPC Amount', 'ppc_amount', 'prev_ppc_amount'),
                    ('Conversion %', 'conversion_percent', 'prev_conversion_percent'),
                    ('NPC', 'npc', 'prev_npc'),
                    ('Referral Visits', 'referral_visits', 'prev_referral_visits'),
                    (('PC + P1 Joined', 'PC+P1 Joined'), 'pc_p_joined', 'prev_pc_p_joined'),
                ),
            )
        except Exception:
            _logger.exception("BM Users sync failed")
