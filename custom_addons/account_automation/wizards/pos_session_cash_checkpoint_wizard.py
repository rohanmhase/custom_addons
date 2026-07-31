from odoo import models, fields, api, _
from odoo.exceptions import UserError, ValidationError
from datetime import timedelta, datetime, time


# ======================================================================
# SINGLE-CLINIC WIZARD
# ======================================================================
class PosSessionCashCheckpointWizard(models.TransientModel):
    _name = 'pos.session.cash.checkpoint.wizard'
    _description = 'Update Single-Clinic Cash Checkpoint'

    clinic_id = fields.Many2one(
        'pos.config', string='Clinic', required=True)
    session_id = fields.Many2one(
        'pos.session', string='Source Session', required=True,
        domain="[('config_id', '=', clinic_id), ('state', '=', 'closed')]",
        help="Latest closed session up to which we're setting the checkpoint.")

    current_checkpoint_id = fields.Many2one(
        'pos.session.cash.checkpoint',
        compute='_compute_info', store=False)
    current_checkpoint_datetime = fields.Datetime(compute='_compute_info', store=False)
    current_checkpoint_amount = fields.Float(compute='_compute_info', store=False)
    backward_error = fields.Char(compute='_compute_info', store=False, readonly=True)

    session_stop_at = fields.Datetime(compute='_compute_session_info', store=False, readonly=True)
    session_entered_closing = fields.Float(compute='_compute_session_info', store=False, readonly=True)
    session_expected_closing = fields.Float(compute='_compute_expected', store=False, readonly=True)
    session_diff = fields.Float(compute='_compute_expected', store=False, readonly=True)
    diff_warning = fields.Char(compute='_compute_expected', store=False, readonly=True)

    notes = fields.Text(string='Notes')

    @api.depends('session_id')
    def _compute_session_info(self):
        for rec in self:
            rec.session_stop_at = rec.session_id.stop_at if rec.session_id else False
            rec.session_entered_closing = (
                rec.session_id.cash_register_balance_end_real
                if rec.session_id else 0.0
            )

    @api.depends('clinic_id', 'session_id')
    def _compute_info(self):
        Chk = self.env['pos.session.cash.checkpoint']
        for rec in self:
            active = Chk.search([
                ('clinic_id', '=', rec.clinic_id.id),
                ('active', '=', True),
            ], limit=1)
            rec.current_checkpoint_id = active
            rec.current_checkpoint_datetime = active.checkpoint_datetime if active else False
            rec.current_checkpoint_amount = active.checkpoint_amount if active else 0.0

            if active and rec.session_id and rec.session_id.stop_at:
                if rec.session_id.stop_at <= active.checkpoint_datetime:
                    rec.backward_error = _(
                        "Selected session (closed %s) is not newer than "
                        "current active checkpoint (%s). Use 'Undo Last Update' instead."
                    ) % (
                        rec.session_id.stop_at.strftime('%d %b %Y %H:%M'),
                        active.checkpoint_datetime.strftime('%d %b %Y %H:%M'),
                    )
                else:
                    rec.backward_error = False
            else:
                rec.backward_error = False

    @api.depends('session_id')
    def _compute_expected(self):
        Service = self.env['pos.session.alert.service']
        for rec in self:
            rec.session_expected_closing = 0.0
            rec.session_diff = 0.0
            rec.diff_warning = False
            if rec.session_id:
                data = Service.compute_expected_batch([rec.session_id.id])
                info = data.get(rec.session_id.id, {})
                rec.session_expected_closing = info.get('expected_closing', 0.0)
                entered = rec.session_id.cash_register_balance_end_real or 0.0
                rec.session_diff = entered - rec.session_expected_closing
                if abs(rec.session_diff) > 1.0:
                    rec.diff_warning = _(
                        "⚠ This session has a difference of ₹%.2f between entered and expected. "
                        "The checkpoint will use the EXPECTED value (₹%.2f). "
                        "This is normal — proceed if you have verified the amount physically."
                    ) % (rec.session_diff, rec.session_expected_closing)

    def action_confirm(self):
        self.ensure_one()
        if self.backward_error:
            raise UserError(self.backward_error)
        if not self.session_id:
            raise UserError(_("Please select a source session."))
        if not self.session_expected_closing:
            raise UserError(_(
                "Cannot compute expected closing for this session. "
                "The checkpoint requires a valid expected value."
            ))

        new_cp = self.env['pos.session.cash.checkpoint'].create({
            'clinic_id': self.clinic_id.id,
            'checkpoint_datetime': self.session_id.stop_at,
            'checkpoint_amount': self.session_expected_closing,
            'source_session_id': self.session_id.id,
            'checkpoint_type': 'manual',
            'active': True,
            'previous_checkpoint_id': self.current_checkpoint_id.id if self.current_checkpoint_id else False,
            'notes': self.notes or False,
        })
        return {
            'type': 'ir.actions.client',
            'tag': 'display_notification',
            'params': {
                'title': _('Checkpoint Updated'),
                'message': _('New checkpoint for %s: ₹%.2f (%s)') % (
                    self.clinic_id.name, new_cp.checkpoint_amount,
                    new_cp.checkpoint_datetime.strftime('%d %b %Y %H:%M')
                ),
                'type': 'success',
            }
        }


# ======================================================================
# BULK WIZARD
# ======================================================================
class PosSessionCashCheckpointBulkWizard(models.TransientModel):
    _name = 'pos.session.cash.checkpoint.bulk.wizard'
    _description = 'Bulk Update Cash Checkpoints'

    effective_date = fields.Date(
        string='Effective Date', required=True,
        default=lambda self: fields.Date.context_today(self) - timedelta(days=1),
        help="Last verified business day. Checkpoints will be set to sessions closed on or before this date."
    )
    scope = fields.Selection([
        ('overdue', 'Only overdue clinics (checkpoint > 30 days old)'),
        ('all', 'All clinics with an eligible session'),
    ], default='all', required=True)
    skip_high_diff = fields.Boolean(
        string='Auto-skip high diff', default=False,
        help="Uncheck rows where entered vs expected differs by more than the threshold."
    )
    skip_diff_threshold = fields.Float(default=1000.0)
    notes = fields.Text(string='Notes (applied to all new checkpoints)')

    preview_loaded = fields.Boolean(default=False)
    line_ids = fields.One2many(
        'pos.session.cash.checkpoint.bulk.wizard.line',
        'wizard_id', string='Preview')
    skipped_no_session = fields.Integer(readonly=True)
    skipped_backward = fields.Integer(readonly=True)

    @api.constrains('effective_date')
    def _check_effective_date_not_today_or_future(self):
        today = fields.Date.context_today(self)
        for rec in self:
            if rec.effective_date and rec.effective_date >= today:
                raise ValidationError(_(
                    "Effective Date must be in the past (maximum: yesterday)."
                ))

    def action_load_preview(self):
        self.ensure_one()
        self.line_ids.unlink()

        eod = datetime.combine(self.effective_date, time(23, 59, 59))

        self.env.cr.execute("""
            SELECT DISTINCT ON (ps.config_id)
                ps.config_id,
                ps.id AS session_id,
                ps.stop_at,
                ps.cash_register_balance_end_real
            FROM pos_session ps
            JOIN pos_config pc ON pc.id = ps.config_id
            WHERE pc.active = TRUE
              AND ps.state = 'closed'
              AND ps.stop_at <= %s
            ORDER BY ps.config_id, ps.stop_at DESC
        """, (eod,))
        candidates = self.env.cr.fetchall()

        self.env.cr.execute("SELECT id FROM pos_config WHERE active = TRUE")
        active_clinics = {r[0] for r in self.env.cr.fetchall()}
        clinics_with_candidate = {r[0] for r in candidates}
        no_session_count = len(active_clinics - clinics_with_candidate)

        if not candidates:
            self.write({
                'preview_loaded': True,
                'skipped_no_session': no_session_count,
                'skipped_backward': 0,
            })
            return {'type': 'ir.actions.act_window',
                    'res_model': self._name, 'res_id': self.id,
                    'view_mode': 'form', 'target': 'new'}

        session_ids = [c[1] for c in candidates]
        expected_map = self.env['pos.session.alert.service'].sudo().compute_expected_batch(session_ids)

        Chk = self.env['pos.session.cash.checkpoint']
        active_cps = Chk.search([('active', '=', True)])
        cp_map = {cp.clinic_id.id: cp for cp in active_cps}

        thirty_days_ago = fields.Datetime.now() - timedelta(days=30)
        lines_vals = []
        backward_count = 0

        for config_id, session_id, stop_at, entered_closing in candidates:
            current_cp = cp_map.get(config_id)

            if self.scope == 'overdue':
                if current_cp and current_cp.checkpoint_datetime and current_cp.checkpoint_datetime >= thirty_days_ago:
                    continue

            is_backward = False
            if current_cp and current_cp.checkpoint_datetime and stop_at <= current_cp.checkpoint_datetime:
                is_backward = True
                backward_count += 1

            info = expected_map.get(session_id, {})
            expected_close = info.get('expected_closing', 0.0)
            diff = (entered_closing or 0.0) - expected_close

            warn = ''
            if is_backward:
                warn = 'Would move checkpoint backward — use Undo instead'
            elif self.skip_high_diff and abs(diff) > (self.skip_diff_threshold or 0):
                warn = 'High diff (auto-skipped)'
            elif abs(diff) > (self.skip_diff_threshold or 0):
                warn = 'High diff'

            include = not is_backward
            if self.skip_high_diff and abs(diff) > (self.skip_diff_threshold or 0):
                include = False

            lines_vals.append((0, 0, {
                'clinic_id': config_id,
                'session_id': session_id,
                'session_stop_at_stored': stop_at,
                'entered_closing': entered_closing or 0.0,
                'expected_closing': expected_close,
                'diff': diff,
                'current_checkpoint_datetime': current_cp.checkpoint_datetime if current_cp else False,
                'current_checkpoint_amount': current_cp.checkpoint_amount if current_cp else 0.0,
                'is_backward': is_backward,
                'include': include,
                'warning': warn,
            }))

        self.write({
            'line_ids': lines_vals,
            'preview_loaded': True,
            'skipped_no_session': no_session_count,
            'skipped_backward': backward_count,
        })
        return {'type': 'ir.actions.act_window',
                'res_model': self._name, 'res_id': self.id,
                'view_mode': 'form', 'target': 'new'}

    def action_confirm_bulk(self):
        self.ensure_one()
        if not self.preview_loaded:
            raise UserError(_("Please load the preview first."))

        Chk = self.env['pos.session.cash.checkpoint']
        created = 0
        skipped = 0
        errors = 0

        for line in self.line_ids:
            if not line.include or line.is_backward:
                skipped += 1
                continue
            try:
                session_sudo = line.session_id.sudo()
                current = Chk.search([
                    ('clinic_id', '=', line.clinic_id.id),
                    ('active', '=', True),
                ], limit=1)
                Chk.create({
                    'clinic_id': line.clinic_id.id,
                    'checkpoint_datetime': line.session_stop_at_stored,
                    'checkpoint_amount': line.expected_closing,
                    'source_session_id': session_sudo.id,
                    'checkpoint_type': 'manual',
                    'active': True,
                    'previous_checkpoint_id': current.id if current else False,
                    'notes': self.notes or False,
                })
                created += 1
            except Exception:
                errors += 1

        return {
            'type': 'ir.actions.client',
            'tag': 'display_notification',
            'params': {
                'title': _('Bulk Update Complete'),
                'message': _('Created: %(c)s | Skipped: %(s)s | Errors: %(e)s') % {
                    'c': created, 's': skipped, 'e': errors,
                },
                'type': 'success',
                'sticky': True,
            }
        }


class PosSessionCashCheckpointBulkWizardLine(models.TransientModel):
    _name = 'pos.session.cash.checkpoint.bulk.wizard.line'
    _description = 'Bulk Checkpoint Preview Line'

    wizard_id = fields.Many2one(
        'pos.session.cash.checkpoint.bulk.wizard', ondelete='cascade', required=True)
    clinic_id = fields.Many2one('pos.config', string='Clinic', readonly=True)
    session_id = fields.Many2one('pos.session', string='Session', readonly=True)
    session_name = fields.Char(related='session_id.name', readonly=True, compute_sudo=True)
    session_stop_at = fields.Datetime(related='session_id.stop_at', readonly=True, compute_sudo=True)
    session_stop_at_stored = fields.Datetime(string='Session Stop At', readonly=True)
    entered_closing = fields.Float(string='Entered', readonly=True)
    expected_closing = fields.Float(string='Expected (New Anchor)', readonly=True)
    diff = fields.Float(string='Diff', readonly=True)
    current_checkpoint_datetime = fields.Datetime(string='Current Anchor Date', readonly=True)
    current_checkpoint_amount = fields.Float(string='Current Anchor', readonly=True)
    is_backward = fields.Boolean(readonly=True)
    include = fields.Boolean(string='Include', default=True)
    warning = fields.Char(readonly=True)