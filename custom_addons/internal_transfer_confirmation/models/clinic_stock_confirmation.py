# clinic_stock_confirmation.py
import logging
from collections import defaultdict

from odoo import models, fields, api, _, SUPERUSER_ID
from odoo.exceptions import UserError

_logger = logging.getLogger(__name__)


class ClinicStockConfirmationDashboard(models.TransientModel):
    _name = 'clinic.stock.confirmation.dashboard'
    _description = 'Stock Confirmation Dashboard'

    display_name = fields.Char(compute='_compute_display_name')

    def _compute_display_name(self):
        for record in self:
            record.display_name = 'Stock Confirmation'

    def action_open_current_stock_confirmation(self):
        return {
            'type': 'ir.actions.act_window',
            'name': 'Select Clinic',
            'res_model': 'clinic.selection.wizard',
            'view_mode': 'form',
            'target': 'new',
        }

    def action_open_report(self):
        return {
            'type': 'ir.actions.act_window',
            'name': 'Warehouse Report',
            'res_model': 'clinic.warehouse.report',
            'view_mode': 'form',
            'target': 'current',
            'context': {},
        }

    def action_open_internal_transfer(self):
        return {
            'type': 'ir.actions.act_window',
            'name': 'Select Clinic',
            'res_model': 'clinic.transfer.selection.wizard',
            'view_mode': 'form',
            'target': 'new',
        }


class ClinicStockConfirmation(models.Model):
    _name = 'clinic.stock.confirmation'
    _description = 'Clinic Stock Confirmation'
    _inherit = ['mail.thread', 'mail.activity.mixin']
    _order = 'create_date desc, destination_warehouse_id, product_id'

    replenishment_id = fields.Many2one(
        'clinic.stock.replenishment',
        string="Replenishment",
        readonly=True,
        ondelete='set null',
        index=True,
    )
    replenishment_log_id = fields.Many2one(
        'clinic.stock.replenishment.log',
        string="Log Entry",
        readonly=True,
        ondelete='set null',
    )
    replenishment_date = fields.Datetime(
        string="Date",
        related='replenishment_id.create_date',
        store=True,
        readonly=True,
        index=True,
    )
    destination_warehouse_id = fields.Many2one(
        'stock.warehouse',
        string="Clinic",
        readonly=True,
        index=True,
    )
    product_id = fields.Many2one(
        'product.product',
        string="Medicine",
        readonly=True,
        index=True,
    )
    target_qty = fields.Integer(string="Target Qty", readonly=True)
    current_stock = fields.Integer(string="Current Stock", readonly=True)
    shortage_qty = fields.Integer(string="Shortage Qty", readonly=True)
    correct_quantity = fields.Integer(string="Correct Quantity", default=0)
    additional_quantity = fields.Integer(string="Additional Quantity", default=0)
    revised_shortage = fields.Integer(
        string="Revised Shortage",
        compute='_compute_revised_shortage',
    )

    @api.depends('target_qty', 'correct_quantity')
    def _compute_revised_shortage(self):
        for rec in self:
            diff = (rec.target_qty or 0) - (rec.correct_quantity or 0)
            rec.revised_shortage = max(diff, 0)

    state = fields.Selection([
        ('pending', 'Pending'),
        ('confirmed', 'Confirmed'),
    ], default='pending', string="Status", index=True)

    confirmed_by = fields.Many2one('res.users', string="Confirmed By", readonly=True)
    confirmation_date = fields.Datetime(string="Confirmed On", readonly=True)
    active = fields.Boolean(default=True)

    problem_raised = fields.Boolean(string="Problem Raised", default=False)
    problem_type = fields.Selection([
        ('wrong_current_stock', 'Wrong Current Stock'),
        ('wrong_shortage', 'Wrong Shortage'),
    ], string="Problem Type")
    corrected_current_stock = fields.Integer(string="Correct Current Stock")
    requested_additional_qty = fields.Integer(string="Need Additional Qty")
    problem_notes = fields.Text(string="Notes")

    resolution_status = fields.Selection([
        ('unresolved', 'Unresolved'),
        ('resolved', 'Resolved'),
        ('skipped', 'Skipped'),
    ], string="Resolution Status", index=True)

    resolution_notes = fields.Text(string="Resolution Notes")
    resolved_by = fields.Many2one('res.users', string="Resolved By", readonly=True)
    resolved_date = fields.Datetime(string="Resolved On", readonly=True)

    is_problem = fields.Boolean(
        string="Is Problem",
        compute='_compute_is_problem',
        store=True,
        index=True,
    )

    @api.depends('problem_raised', 'correct_quantity', 'additional_quantity', 'problem_notes')
    def _compute_is_problem(self):
        for rec in self:
            rec.is_problem = bool(
                rec.problem_raised
                or (rec.correct_quantity and rec.correct_quantity > 0)
                or (rec.additional_quantity and rec.additional_quantity > 0)
                or (rec.problem_notes and rec.problem_notes.strip())
            )

    @api.model
    def _get_batch_recipient_map(self, warehouse_ids):
        recipient_map = {}
        if not warehouse_ids:
            return recipient_map

        clinics = self.env['clinic.clinic'].sudo().search([
            ('warehouse_id', 'in', list(warehouse_ids)),
            ('pos_config_id', '!=', False),
        ])
        if not clinics:
            return recipient_map

        warehouse_clinic_map = {
            clinic.warehouse_id.id: clinic
            for clinic in clinics
            if clinic.warehouse_id
        }

        config_ids = clinics.mapped('pos_config_id').ids
        if not config_ids:
            return recipient_map

        sessions = self.env['pos.session'].sudo().search([
            ('config_id', 'in', config_ids),
            ('state', 'in', ['opening_control', 'opened', 'closing_control']),
        ], order='id desc')

        latest_session_by_config = {}
        for session in sessions:
            config_id = session.config_id.id
            if config_id not in latest_session_by_config:
                latest_session_by_config[config_id] = session

        for warehouse_id, clinic in warehouse_clinic_map.items():
            session = latest_session_by_config.get(clinic.pos_config_id.id)
            user = session.user_id if session and session.user_id and session.user_id.active else False
            recipient_map[warehouse_id] = {
                'user_id': user.id if user else False,
                'email': (user.login or user.partner_id.email) if user else False,
            }

        return recipient_map

    def _notify_new_confirmation_batches(self):
        Confirmation = self.env['clinic.stock.confirmation']
        grouped = defaultdict(lambda: Confirmation.browse())

        pending_records = self.filtered(
            lambda r: r.active and r.state == 'pending' and r.destination_warehouse_id and r.replenishment_id
        )
        if not pending_records:
            return True

        for rec in pending_records:
            grouped[(rec.destination_warehouse_id.id, rec.replenishment_id.id)] |= rec

        recipient_map = self._get_batch_recipient_map(
            {warehouse_id for warehouse_id, _replenishment_id in grouped.keys()}
        )
        template = self.env.ref(
            'internal_transfer_confirmation.email_template_stock_confirmation_request',
            raise_if_not_found=False,
        )
        activity_type = self.env.ref('mail.mail_activity_data_todo', raise_if_not_found=False)
        model_id = self.env['ir.model']._get_id('clinic.stock.confirmation')

        for (warehouse_id, replenishment_id), batch_lines in grouped.items():
            anchor = batch_lines[0]
            recipient = recipient_map.get(warehouse_id, {})
            replenishment_name = anchor.replenishment_id.display_name or anchor.replenishment_id.name

            if template and recipient.get('email'):
                line_payload = []
                for line in batch_lines.sorted(key=lambda r: r.product_id.display_name or ''):
                    line_payload.append({
                        'product_name': line.product_id.display_name,
                        'target_qty': line.target_qty,
                        'current_stock': line.current_stock,
                        'shortage_qty': line.shortage_qty,
                    })

                try:
                    mail_values = template.sudo().with_context(
                        warehouse_name=anchor.destination_warehouse_id.display_name,
                        replenishment_name=replenishment_name,
                        confirmation_lines=line_payload,
                        line_count=len(batch_lines),
                    )._generate_template(
                        [anchor.id],
                        ('subject', 'body_html', 'email_from')
                    )[anchor.id]

                    self.env['mail.mail'].sudo().create({
                        'subject': mail_values.get('subject'),
                        'body_html': mail_values.get('body_html'),
                        'email_from': mail_values.get('email_from') or 'noreply@researchayu.com',
                        'email_to': recipient['email'],
                        'auto_delete': True,
                    })
                except Exception:
                    _logger.exception(
                        "Failed to queue stock confirmation email for warehouse %s, replenishment %s",
                        warehouse_id,
                        replenishment_id,
                    )

            user_id = recipient.get('user_id')
            if activity_type and model_id and user_id:
                summary = f'Stock confirmation required: {replenishment_name}'

                existing = self.env['mail.activity'].sudo().search([
                    ('activity_type_id', '=', activity_type.id),
                    ('res_model_id', '=', model_id),
                    ('res_id', '=', anchor.id),
                    ('user_id', '=', user_id),
                    ('summary', '=', summary),
                ], limit=1)

                if not existing:
                    self.env['mail.activity'].sudo().create({
                        'activity_type_id': activity_type.id,
                        'summary': summary,
                        'note': _(
                            '<p>Please confirm stock for <strong>%s</strong>.</p>'
                            '<p><strong>CSR:</strong> %s</p>'
                            '<p><strong>Items:</strong> %s</p>'
                        ) % (
                                    anchor.destination_warehouse_id.display_name,
                                    replenishment_name,
                                    len(batch_lines),
                                ),
                        'date_deadline': fields.Date.context_today(self),
                        'user_id': user_id,
                        'res_id': anchor.id,
                        'res_model_id': model_id,
                    })

        return True

    def _close_request_activities_for_completed_batches(self, feedback_message=None):
        if not self:
            return True

        activity_type = self.env.ref('mail.mail_activity_data_todo', raise_if_not_found=False)
        model_id = self.env['ir.model']._get_id('clinic.stock.confirmation')
        if not activity_type or not model_id:
            return True

        batch_keys = {
            (rec.destination_warehouse_id.id, rec.replenishment_id.id)
            for rec in self
            if rec.destination_warehouse_id and rec.replenishment_id
        }

        for clinic_id, replenishment_id in batch_keys:
            batch_lines = self.search([
                ('destination_warehouse_id', '=', clinic_id),
                ('replenishment_id', '=', replenishment_id),
                ('active', '=', True),
            ])

            if batch_lines.filtered(lambda l: l.state == 'pending'):
                continue

            activities = self.env['mail.activity'].sudo().search([
                ('activity_type_id', '=', activity_type.id),
                ('res_model_id', '=', model_id),
                ('res_id', 'in', batch_lines.ids),
                ('summary', 'like', 'Stock confirmation required:%'),
            ])
            if activities:
                activities.action_feedback(
                    feedback=feedback_message or _('Stock confirmation completed.')
                )

        return True

    def _cancel_request_activities_for_archived(self):
        if not self:
            return True

        activity_type = self.env.ref('mail.mail_activity_data_todo', raise_if_not_found=False)
        model_id = self.env['ir.model']._get_id('clinic.stock.confirmation')
        if not activity_type or not model_id:
            return True

        activities = self.env['mail.activity'].sudo().search([
            ('activity_type_id', '=', activity_type.id),
            ('res_model_id', '=', model_id),
            ('res_id', 'in', self.ids),
            ('summary', 'like', 'Stock confirmation required:%'),
        ])
        if activities:
            activities.action_feedback(
                feedback=_('Superseded by a newer replenishment request.')
            )

        return True

    def create_from_replenishment(self, replenishment):
        Log = self.env['clinic.stock.replenishment.log']
        logs = Log.search([('replenishment_id', '=', replenishment.id)])
        vals_list = []
        for log in logs:
            vals_list.append({
                'replenishment_id': replenishment.id,
                'replenishment_log_id': log.id,
                'destination_warehouse_id': log.destination_warehouse_id.id,
                'product_id': log.product_id.id,
                'target_qty': log.target_qty,
                'current_stock': log.current_stock,
                'shortage_qty': log.shortage_qty,
                'state': 'pending',
            })
        if vals_list:
            created = self.create(vals_list)
            created._notify_new_confirmation_batches()

    def action_raise_problem(self):
        self.ensure_one()
        return {
            'type': 'ir.actions.act_window',
            'name': 'Raise Problem',
            'res_model': 'clinic.stock.confirmation',
            'res_id': self.id,
            'view_mode': 'form',
            'target': 'new',
            'context': {
                'form_view_initial_mode': 'edit',
                'create': False,
            },
        }

    @api.onchange('correct_quantity')
    def _onchange_correct_quantity(self):
        if self.correct_quantity and self.correct_quantity > 0:
            self.additional_quantity = 0

    @api.onchange('additional_quantity')
    def _onchange_additional_quantity(self):
        if self.additional_quantity and self.additional_quantity > 0:
            self.correct_quantity = 0

    def action_confirm_all(self):
        clinic_id = self.env.context.get('active_clinic_id')
        replenishment_id = self.env.context.get('active_replenishment_id')

        domain = [
            ('state', '=', 'pending'),
            ('problem_raised', '=', False),
            ('active', '=', True),
        ]
        if clinic_id:
            domain.append(('destination_warehouse_id', '=', clinic_id))
        if replenishment_id:
            domain.append(('replenishment_id', '=', replenishment_id))

        if not clinic_id and not replenishment_id:
            active_ids = self.env.context.get('active_ids', [])
            if active_ids:
                domain.append(('id', 'in', active_ids))
            else:
                raise UserError(
                    "Cannot determine which clinic to confirm. Please open through the Stock Confirmation wizard."
                )

        records = self.search(domain)
        if records:
            records.write({
                'state': 'confirmed',
                'confirmed_by': self.env.user.id,
                'confirmation_date': fields.Datetime.now(),
            })
            records._close_request_activities_for_completed_batches()

        return {'type': 'ir.actions.client', 'tag': 'reload'}

    def action_confirm(self):
        for rec in self:
            rec.write({
                'state': 'confirmed',
                'confirmed_by': self.env.user.id,
                'confirmation_date': fields.Datetime.now(),
                'problem_raised': True,
                'resolution_status': 'unresolved',
            })

        self._close_request_activities_for_completed_batches()
        return True

    def action_mark_resolved(self):
        self.ensure_one()
        wizard = self.env['clinic.resolve.problem.wizard'].create({
            'confirmation_id': self.id,
            'resolution_notes': self.resolution_notes or '',
        })
        return {
            'type': 'ir.actions.act_window',
            'name': 'Resolve Problem',
            'res_model': 'clinic.resolve.problem.wizard',
            'res_id': wizard.id,
            'view_mode': 'form',
            'target': 'new',
            'context': {
                'form_view_initial_mode': 'edit',
                'create': False,
                'edit': True,
            },
        }

    def action_skip_problem(self):
        for rec in self:
            rec.write({
                'resolution_status': 'skipped',
                'resolved_by': self.env.user.id,
                'resolved_date': fields.Datetime.now(),
            })
        return True

    def action_reopen_problem(self):
        for rec in self:
            rec.write({
                'resolution_status': 'unresolved',
                'resolved_by': False,
                'resolved_date': False,
                'resolution_notes': False,
            })
        return True

    @api.model
    def _archive_orphaned_confirmations(self):
        orphaned = self.search([
            ('replenishment_id', '=', False),
            ('active', '=', True),
        ])
        if orphaned:
            orphaned._cancel_request_activities_for_archived()
            orphaned.write({'active': False})

        archived_replenishments = self.env['clinic.stock.replenishment'].with_context(
            active_test=False
        ).search([('active', '=', False)])

        if archived_replenishments:
            linked = self.search([
                ('replenishment_id', 'in', archived_replenishments.ids),
                ('active', '=', True),
            ])
            if linked:
                linked._cancel_request_activities_for_archived()
                linked.write({'active': False})
        return True

    def unlink(self):
        self._cancel_request_activities_for_archived()
        self.write({'active': False})
        return True


class ClinicResolveProblemWizard(models.TransientModel):
    _name = 'clinic.resolve.problem.wizard'
    _description = 'Warehouse Resolve Stock Problem Wizard'

    confirmation_id = fields.Many2one(
        'clinic.stock.confirmation', required=True, readonly=True
    )
    product_name = fields.Char(
        related='confirmation_id.product_id.name', readonly=True, string="Medicine"
    )
    clinic_name = fields.Char(
        related='confirmation_id.destination_warehouse_id.name', readonly=True, string="Clinic"
    )
    clinic_note = fields.Text(
        related='confirmation_id.problem_notes', readonly=True, string="Clinic's Note"
    )
    correct_quantity = fields.Integer(
        related='confirmation_id.correct_quantity', readonly=True
    )
    additional_quantity = fields.Integer(
        related='confirmation_id.additional_quantity', readonly=True
    )
    resolution_notes = fields.Text(string="Resolution Note")

    def action_confirm_resolve(self):
        self.ensure_one()
        self.confirmation_id.write({
            'resolution_status': 'resolved',
            'resolution_notes': self.resolution_notes,
            'resolved_by': self.env.user.id,
            'resolved_date': fields.Datetime.now(),
        })
        return {'type': 'ir.actions.act_window_close'}


class ClinicStockReplenishmentLogInherit(models.Model):
    _inherit = 'clinic.stock.replenishment.log'

    @api.model_create_multi
    def create(self, vals_list):
        logs = super().create(vals_list)
        Confirmation = self.env['clinic.stock.confirmation']

        clinic_ids = list({
            log.destination_warehouse_id.id
            for log in logs
            if log.destination_warehouse_id
        })

        if clinic_ids:
            old_pending = Confirmation.search([
                ('destination_warehouse_id', 'in', clinic_ids),
                ('state', '=', 'pending'),
                ('active', '=', True),
            ])
            if old_pending:
                old_pending._cancel_request_activities_for_archived()
                old_pending.write({'active': False})

        conf_vals = []
        for log in logs:
            conf_vals.append({
                'replenishment_id': log.replenishment_id.id,
                'replenishment_log_id': log.id,
                'destination_warehouse_id': log.destination_warehouse_id.id,
                'product_id': log.product_id.id,
                'target_qty': log.target_qty,
                'current_stock': log.current_stock,
                'shortage_qty': log.shortage_qty,
                'state': 'pending',
            })

        created_confirmations = Confirmation.browse()
        if conf_vals:
            created_confirmations = Confirmation.create(conf_vals)
            created_confirmations._notify_new_confirmation_batches()

        return logs

    def unlink(self):
        confirmations = self.env['clinic.stock.confirmation'].search([
            ('replenishment_log_id', 'in', self.ids),
            ('active', '=', True),
        ])
        if confirmations:
            confirmations._cancel_request_activities_for_archived()
            confirmations.write({'active': False})
        return super().unlink()


class ClinicStockReplenishmentInherit(models.Model):
    _inherit = 'clinic.stock.replenishment'

    def unlink(self):
        confirmations = self.env['clinic.stock.confirmation'].search([
            ('replenishment_id', 'in', self.ids),
            ('active', '=', True),
        ])
        if confirmations:
            confirmations._cancel_request_activities_for_archived()
            confirmations.write({'active': False})
        return super().unlink()

    def write(self, vals):
        result = super().write(vals)
        if 'active' in vals and vals['active'] is False:
            confirmations = self.env['clinic.stock.confirmation'].search([
                ('replenishment_id', 'in', self.ids),
                ('active', '=', True),
            ])
            if confirmations:
                confirmations._cancel_request_activities_for_archived()
                confirmations.write({'active': False})
        return result


class ClinicStockConfirmationAutoConfirm(models.AbstractModel):
    _name = 'clinic.stock.confirmation.autoconfirm'
    _description = 'Auto Confirm Cron Helper'

    @api.model
    def _cron_auto_confirm_pending(self):
        """Auto-confirm pending stock confirmations as OdooBot."""
        Confirmation = self.env['clinic.stock.confirmation']

        pending = Confirmation.search([
            ('state', '=', 'pending'),
            ('active', '=', True),
            ('problem_raised', '=', False),
        ])

        if pending:
            pending.write({
                'state': 'confirmed',
                'confirmed_by': SUPERUSER_ID,
                'confirmation_date': fields.Datetime.now(),
            })
            pending._close_request_activities_for_completed_batches(
                feedback_message=_('Auto-confirmed by system (no response from clinic).')
            )
            _logger.info("Auto-confirmed %s pending stock confirmations", len(pending))

        return True
