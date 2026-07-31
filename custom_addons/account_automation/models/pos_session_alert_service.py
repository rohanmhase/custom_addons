import logging
from datetime import timedelta
from odoo import models, fields, api, _

_logger = logging.getLogger(__name__)


class PosSessionAlertService(models.AbstractModel):
    _name = 'pos.session.alert.service'
    _description = 'POS Session Cash Alert Service'

    # ------------------------------------------------------------------
    # BATCH: compute expected opening/closing for many sessions in ONE query
    # Uses per-clinic active checkpoint (fallback to April 1 default)
    # ------------------------------------------------------------------
    @api.model
    def compute_expected_batch(self, session_ids):
        if not session_ids:
            return {}

        self.env.cr.execute("""
            SELECT DISTINCT config_id FROM pos_session WHERE id IN %s
        """, (tuple(session_ids),))
        clinic_ids = tuple(r[0] for r in self.env.cr.fetchall())
        if not clinic_ids:
            return {}

        self.env.cr.execute("""
            WITH effective_anchor AS (
                SELECT
                    pc.id AS config_id,
                    COALESCE(
                        (SELECT cp.checkpoint_datetime
                         FROM pos_session_cash_checkpoint cp
                         WHERE cp.clinic_id = pc.id AND cp.active = TRUE
                         LIMIT 1),
                        '2026-04-01 00:00:00'::timestamp
                    ) AS anchor_dt,
                    COALESCE(
                        (SELECT cp.checkpoint_amount
                         FROM pos_session_cash_checkpoint cp
                         WHERE cp.clinic_id = pc.id AND cp.active = TRUE
                         LIMIT 1),
                        (SELECT COALESCE(ps.cash_register_balance_start, 0)
                         FROM pos_session ps
                         WHERE ps.config_id = pc.id
                           AND (ps.start_at AT TIME ZONE 'UTC' AT TIME ZONE 'Asia/Kolkata')::date = '2026-04-01'::date
                         ORDER BY ps.start_at ASC LIMIT 1),
                        0
                    ) AS anchor_amt
                FROM pos_config pc
                WHERE pc.id IN %(clinics)s
            ),
            pos_cash_totals AS (
                SELECT
                    po.session_id,
                    SUM(CASE WHEN cf.method_sum > 0 THEN cf.method_sum ELSE 0 END) AS patient_cash,
                    SUM(CASE WHEN cf.method_sum < 0 THEN ABS(cf.method_sum) ELSE 0 END) AS cash_refunds
                FROM pos_order po
                JOIN account_move am ON am.id = po.account_move
                JOIN (
                    SELECT
                        am_inner.id AS inv_id,
                        CASE WHEN aml_inv.id = apr.debit_move_id THEN apr.amount
                             ELSE -apr.amount END AS method_sum
                    FROM account_move am_inner
                    JOIN account_move_line aml_inv ON aml_inv.move_id = am_inner.id
                    JOIN account_account acc ON acc.id = aml_inv.account_id
                    JOIN account_partial_reconcile apr
                        ON (apr.debit_move_id = aml_inv.id OR apr.credit_move_id = aml_inv.id)
                    JOIN account_move_line aml_pay ON (
                        (aml_pay.id = apr.credit_move_id AND aml_pay.id <> aml_inv.id) OR
                        (aml_pay.id = apr.debit_move_id AND aml_pay.id <> aml_inv.id)
                    )
                    JOIN account_journal aj ON aj.id = aml_pay.journal_id
                    LEFT JOIN account_payment ap ON ap.id = aml_pay.payment_id
                    LEFT JOIN account_payment_method_line apml ON apml.id = ap.payment_method_line_id
                    LEFT JOIN pos_payment pp ON pp.account_move_id = aml_pay.move_id
                    LEFT JOIN pos_payment_method ppm ON ppm.id = pp.payment_method_id
                    WHERE am_inner.state = 'posted'
                      AND acc.account_type = 'asset_receivable'
                      AND (aj.name->>'en_US' ILIKE '%%cash%%' OR ppm.name->>'en_US' ILIKE '%%cash%%')
                ) cf ON cf.inv_id = am.id
                WHERE am.state = 'posted'
                  AND am.move_type IN ('out_invoice','out_refund')
                GROUP BY po.session_id
            ),
            manual_cash_moves AS (
                SELECT
                    pos_session_id,
                    SUM(CASE WHEN payment_ref ILIKE '%%-In-%%' THEN amount ELSE 0 END) AS manual_in,
                    SUM(CASE WHEN payment_ref ILIKE '%%-Out-%%' THEN ABS(amount) ELSE 0 END) AS manual_out
                FROM account_bank_statement_line
                WHERE (is_replaced = FALSE OR is_replaced IS NULL)
                GROUP BY pos_session_id
            ),
            session_movements AS (
                SELECT
                    ps.id AS session_id,
                    ps.config_id,
                    ps.start_at,
                    (COALESCE(pct.patient_cash, 0)
                     - COALESCE(pct.cash_refunds, 0)
                     + COALESCE(mcm.manual_in, 0)
                     - COALESCE(mcm.manual_out, 0)) AS net_movement,
                    COALESCE(pct.patient_cash, 0)  AS patient_cash,
                    COALESCE(pct.cash_refunds, 0)  AS cash_refunds,
                    COALESCE(mcm.manual_in, 0)     AS manual_in,
                    COALESCE(mcm.manual_out, 0)    AS manual_out
                FROM pos_session ps
                JOIN effective_anchor ea ON ea.config_id = ps.config_id
                LEFT JOIN pos_cash_totals pct ON pct.session_id = ps.id
                LEFT JOIN manual_cash_moves mcm ON mcm.pos_session_id = ps.id
                WHERE ps.config_id IN %(clinics)s
                  AND ps.start_at > ea.anchor_dt
            ),
            session_expected AS (
                SELECT
                    sm.session_id,
                    sm.patient_cash,
                    sm.cash_refunds,
                    sm.manual_in,
                    sm.manual_out,
                    ea.anchor_amt
                    + COALESCE(
                        SUM(sm.net_movement) OVER (
                            PARTITION BY sm.config_id
                            ORDER BY sm.start_at
                            ROWS BETWEEN UNBOUNDED PRECEDING AND 1 PRECEDING
                        ), 0
                    ) AS expected_opening
                FROM session_movements sm
                JOIN effective_anchor ea ON ea.config_id = sm.config_id
            )
            SELECT
                session_id,
                expected_opening,
                patient_cash, cash_refunds, manual_in, manual_out,
                (expected_opening + patient_cash - cash_refunds + manual_in - manual_out)
                    AS expected_closing
            FROM session_expected
            WHERE session_id IN %(target_ids)s
        """, {
            'clinics': clinic_ids,
            'target_ids': tuple(session_ids),
        })

        results = {}
        for row in self.env.cr.dictfetchall():
            results[row['session_id']] = {
                'expected_opening': row['expected_opening'] or 0.0,
                'expected_closing': row['expected_closing'] or 0.0,
                'patient_cash': row['patient_cash'] or 0.0,
                'cash_refunds': row['cash_refunds'] or 0.0,
                'manual_in': row['manual_in'] or 0.0,
                'manual_out': row['manual_out'] or 0.0,
            }
        return results

    # ------------------------------------------------------------------
    # Main entrypoint
    # ------------------------------------------------------------------
    @api.model
    def process_alerts_for_date(self, check_date, force_resend=False):
        config = self.env['pos.session.alert.config'].sudo().get_config()
        if not config.active:
            _logger.info("POS Session Alerts disabled. Skipping.")
            return {'processed': 0, 'emails_sent': 0, 'errors': 0}

        stats = {'processed': 0, 'emails_sent': 0, 'errors': 0}

        self.env.cr.execute("""
            SELECT id, config_id FROM pos_session
            WHERE (start_at AT TIME ZONE 'UTC' AT TIME ZONE 'Asia/Kolkata')::date = %s
        """, (check_date,))
        rows_a = self.env.cr.fetchall()
        session_ids_a = [r[0] for r in rows_a]
        clinic_ids_with_yesterday = {r[1] for r in rows_a}

        exclude = tuple(clinic_ids_with_yesterday) or (0,)
        self.env.cr.execute("""
            SELECT DISTINCT ON (config_id) id FROM pos_session
            WHERE (start_at AT TIME ZONE 'UTC' AT TIME ZONE 'Asia/Kolkata')::date < %s
              AND config_id NOT IN %s
            ORDER BY config_id, start_at DESC
        """, (check_date, exclude))
        step_b_ids = [r[0] for r in self.env.cr.fetchall()]

        all_ids = list(set(session_ids_a + step_b_ids))
        if not all_ids:
            return stats

        expected_map = self.compute_expected_batch(all_ids)
        sessions = self.env['pos.session'].browse(all_ids)

        for session in sessions:
            try:
                expected = expected_map.get(session.id, {
                    'expected_opening': 0.0, 'expected_closing': 0.0,
                    'patient_cash': 0.0, 'cash_refunds': 0.0,
                    'manual_in': 0.0, 'manual_out': 0.0,
                })
                self._process_single_session(session, expected, check_date, config, force_resend, stats)
            except Exception as e:
                stats['errors'] += 1
                _logger.exception("Alert check failed for session %s: %s", session.id, e)
                continue
        return stats

    def _process_single_session(self, session, expected, check_date, config, force_resend, stats):
        stats['processed'] += 1
        tolerance = config.tolerance_amount or 0.0
        issues = []

        entered_open = session.cash_register_balance_start or 0.0
        opening_diff = entered_open - expected['expected_opening']
        if abs(opening_diff) > tolerance:
            if force_resend or not self._alert_exists(session.id, 'opening_diff', check_date):
                issues.append(('opening_diff', opening_diff))

        if session.state == 'closed':
            entered_close = session.cash_register_balance_end_real or 0.0
            closing_diff = entered_close - expected['expected_closing']
            if abs(closing_diff) > tolerance:
                if force_resend or not self._alert_exists(session.id, 'closing_diff', check_date):
                    issues.append(('closing_diff', closing_diff))
        else:
            if force_resend or not self._alert_exists(session.id, 'not_closed', check_date):
                issues.append(('not_closed', None))

        if not issues:
            return

        email_sent, email_error = self._send_consolidated_email(
            session, issues, expected, check_date, config)
        if email_sent:
            stats['emails_sent'] += 1

        LogModel = self.env['pos.session.alert.log'].sudo()
        for alert_type, diff_amount in issues:
            try:
                if force_resend:
                    LogModel.search([
                        ('session_id', '=', session.id),
                        ('alert_type', '=', alert_type),
                        ('check_date', '=', check_date),
                    ]).unlink()
                LogModel.create({
                    'session_id': session.id,
                    'session_name': session.name or '',
                    'clinic_id': session.config_id.id,
                    'clinic_name': session.config_id.name or '',
                    'check_date': check_date,
                    'alert_type': alert_type,
                    'diff_amount': diff_amount or 0.0,
                    'session_state_at_check': session.state,
                    'responsible_user_id': session.user_id.id if session.user_id else False,
                    'responsible_email': session.user_id.partner_id.email if session.user_id else False,
                    'email_sent': email_sent,
                    'email_error': email_error or False,
                })
            except Exception as e:
                _logger.exception("Failed writing log for session %s: %s", session.id, e)

    def _alert_exists(self, session_id, alert_type, check_date):
        return bool(self.env['pos.session.alert.log'].sudo().search_count([
            ('session_id', '=', session_id),
            ('alert_type', '=', alert_type),
            ('check_date', '=', check_date),
        ]))

    def _send_consolidated_email(self, session, issues, expected, check_date, config):
        """Returns (bool sent, str error_or_none). Sends email WITHOUT posting to chatter."""
        try:
            from markupsafe import Markup

            responsible = session.user_id
            if not responsible or not responsible.partner_id.email:
                return (False, "Responsible user has no email")

            cc_emails = ','.join(
                u.partner_id.email for u in config.cc_user_ids if u.partner_id.email
            )

            # Build issues HTML
            issues_html = ""
            for alert_type, diff_amount in issues:
                if alert_type == 'opening_diff':
                    issues_html += (
                        f"<li style='color:#b91c1c;'><b>🔴 Opening Difference:</b> ₹{diff_amount:,.2f} "
                        f"(expected ₹{expected['expected_opening']:,.2f}, "
                        f"entered ₹{session.cash_register_balance_start or 0:,.2f})</li>"
                    )
                elif alert_type == 'closing_diff':
                    issues_html += (
                        f"<li style='color:#b91c1c;'><b>🔴 Closing Difference:</b> ₹{diff_amount:,.2f} "
                        f"(expected ₹{expected['expected_closing']:,.2f}, "
                        f"entered ₹{session.cash_register_balance_end_real or 0:,.2f})</li>"
                    )
                elif alert_type == 'not_closed':
                    age = (fields.Date.context_today(self) - session.start_at.date()).days
                    issues_html += (
                        f"<li style='color:#c2410c;'><b>🟡 Session Not Closed:</b> "
                        f"Open since {session.start_at.strftime('%d %b %Y %H:%M')} "
                        f"({age} day{'s' if age != 1 else ''} ago). Please close it promptly.</li>"
                    )

            # Full HTML body
            body_html = f"""
                <div style="font-family: Arial, sans-serif; color:#111827; max-width:640px;">
                    <h2 style="color:#111827;">POS Session Alert</h2>
                    <p>Hello {responsible.name or 'Cashier'},</p>
                    <p>
                        The following issues were detected for POS session
                        <b>{session.name}</b> at
                        <b>{session.config_id.name}</b>
                        for business day <b>{check_date.strftime('%d %b %Y')}</b>:
                    </p>
                    <ul style="line-height: 1.8;">
                        {issues_html}
                    </ul>
                    <p>Please reconcile as soon as possible.</p>
                    <hr style="border:none;border-top:1px solid #e5e7eb;margin:16px 0;"/>
                    <p style="color:#6b7280;font-size:12px;">
                        This is an automated alert from ResearchAyu Account Automation.
                        Please do not reply.
                    </p>
                </div>
            """

            subject = f"⚠️ Session {session.name} — Issues Detected ({check_date.strftime('%d %b %Y')})"
            from_email = config.from_email or 'noreply@researchayu.com'

            # Create mail.mail directly — bypasses chatter completely
            mail_vals = {
                'subject': subject,
                'body_html': body_html,
                'email_from': from_email,
                'email_to': responsible.partner_id.email,
                'email_cc': cc_emails or False,
                'auto_delete': True,
                # Explicitly no res_model/res_id → no chatter link
            }
            mail = self.env['mail.mail'].sudo().create(mail_vals)
            mail.send(raise_exception=False)

            return (True, None)
        except Exception as e:
            _logger.exception("Email send failed for session %s: %s", session.id, e)
            return (False, str(e))

    @api.model
    def cron_daily_session_alerts(self):
        yesterday = fields.Date.context_today(self) - timedelta(days=1)
        _logger.info("Running daily POS session alert check for %s", yesterday)
        stats = self.process_alerts_for_date(yesterday, force_resend=False)
        _logger.info("POS session alert check done: %s", stats)
        return True
