from odoo import models, fields, api, _
from odoo.exceptions import UserError


class PosSessionCashCheckpoint(models.Model):
    _name = 'pos.session.cash.checkpoint'
    _description = 'POS Session Cash Checkpoint'
    _order = 'clinic_id, checkpoint_datetime desc'
    _rec_name = 'display_name'

    display_name = fields.Char(compute='_compute_display_name', store=False)
    clinic_id = fields.Many2one(
        'pos.config', string='Clinic', required=True, ondelete='restrict', index=True)
    checkpoint_datetime = fields.Datetime(
        string='Verified At', required=True, index=True,
        help="Moment when the cash balance was verified.")
    checkpoint_amount = fields.Float(
        string='Verified Cash Amount', required=True,
        help="Cash amount at checkpoint (always uses expected value, never entered).")
    source_session_id = fields.Many2one(
        'pos.session', string='Source Session', ondelete='set null',
        help="Session whose expected_closing forms this checkpoint.")
    checkpoint_type = fields.Selection([
        ('initial', 'Initial (default)'),
        ('manual', 'Manual Update'),
    ], string='Type', default='manual', required=True)
    active = fields.Boolean(
        string='Active', default=True, index=True,
        help="Only one active checkpoint per clinic at a time.")
    previous_checkpoint_id = fields.Many2one(
        'pos.session.cash.checkpoint', string='Previous Checkpoint',
        ondelete='set null')
    deactivated_by_id = fields.Many2one('res.users', string='Deactivated By')
    deactivated_date = fields.Datetime(string='Deactivated On')
    notes = fields.Text(string='Notes')

    @api.depends('clinic_id', 'checkpoint_datetime', 'checkpoint_amount')
    def _compute_display_name(self):
        for r in self:
            clinic = r.clinic_id.name or ''
            dt = r.checkpoint_datetime and r.checkpoint_datetime.strftime('%d %b %Y %H:%M') or ''
            r.display_name = f"{clinic} — {dt} — ₹{r.checkpoint_amount or 0:,.2f}"

    @api.model_create_multi
    def create(self, vals_list):
        records = super().create(vals_list)
        # Enforce only-one-active per clinic
        for rec in records:
            if rec.active:
                self.env.cr.execute("""
                    UPDATE pos_session_cash_checkpoint
                    SET active = FALSE,
                        deactivated_by_id = %s,
                        deactivated_date = NOW()
                    WHERE clinic_id = %s AND active = TRUE AND id != %s
                """, (self.env.uid, rec.clinic_id.id, rec.id))
        return records

    def action_undo_last_update(self):
        """Deactivate this checkpoint, reactivate the previous one.
        If no previous exists, fall back to April 1 anchor logic
        (by simply deactivating this checkpoint)."""
        self.ensure_one()
        if not self.active:
            raise UserError(_("Only the active checkpoint can be undone."))

        prev = self.previous_checkpoint_id

        if prev:
            # Normal case: reactivate previous
            self.write({
                'active': False,
                'deactivated_by_id': self.env.uid,
                'deactivated_date': fields.Datetime.now(),
            })
            prev.write({
                'active': True,
                'deactivated_by_id': False,
                'deactivated_date': False,
            })
            return {
                'type': 'ir.actions.client',
                'tag': 'display_notification',
                'params': {
                    'title': _('Undo Successful'),
                    'message': _('Restored checkpoint from %s') % prev.display_name,
                    'type': 'success',
                }
            }
        else:
            # No previous → deactivate and fall back to April 1 logic
            self.write({
                'active': False,
                'deactivated_by_id': self.env.uid,
                'deactivated_date': fields.Datetime.now(),
            })
            return {
                'type': 'ir.actions.client',
                'tag': 'display_notification',
                'params': {
                    'title': _('Checkpoint Removed'),
                    'message': _(
                        'No previous checkpoint existed. '
                        'This checkpoint has been deactivated. '
                        'Calculations for %s will fall back to April 1 anchor.'
                    ) % self.clinic_id.name,
                    'type': 'warning',
                    'sticky': True,
                }
            }

    # -------- Utility: get overdue clinics count for dashboard --------
    @api.model
    def get_overdue_count(self):
        """Returns number of clinics whose active checkpoint is >30 days old
        OR clinics that have sessions but no checkpoint at all."""
        self.env.cr.execute("""
            WITH clinic_checkpoints AS (
                SELECT
                    pc.id AS clinic_id,
                    (SELECT checkpoint_datetime
                     FROM pos_session_cash_checkpoint cp
                     WHERE cp.clinic_id = pc.id AND cp.active = TRUE
                     LIMIT 1) AS cp_dt,
                    (SELECT COUNT(*)
                     FROM pos_session ps
                     WHERE ps.config_id = pc.id) AS session_count
                FROM pos_config pc
                WHERE pc.active = TRUE
            )
            SELECT COUNT(*) FROM clinic_checkpoints
            WHERE session_count > 0
              AND (cp_dt IS NULL OR cp_dt < (NOW() - INTERVAL '30 days'))
        """)
        row = self.env.cr.fetchone()
        return row[0] if row else 0
