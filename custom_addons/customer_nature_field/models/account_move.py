import inspect
from odoo import models, fields, api, _
from odoo.exceptions import AccessError

class AccountMove(models.Model):
    _inherit = 'account.move'

    # store=False ensures ZERO columns are added to standard account_move table
    nature_id = fields.Many2one(
        'res.partner.nature',
        string='Nature',
        compute='_compute_nature_id',
        inverse='_inverse_nature_id',
        store=False,
    )

    def _compute_nature_id(self):
        links = self.env['account.move.nature'].sudo().search([('move_id', 'in', self.ids)])
        link_map = {link.move_id.id: link.nature_id for link in links}
        for move in self:
            move.nature_id = link_map.get(move.id, False)

    def _inverse_nature_id(self):
        # Strict Administrator check (Access Rights / Settings)
        if not self.env.user.has_group('base.group_erp_manager'):
            raise AccessError(_("Only users with Administrator (Access Rights) can change the Invoice Nature."))

        Link = self.env['account.move.nature'].sudo()
        for move in self:
            link = Link.search([('move_id', '=', move.id)], limit=1)
            if move.nature_id:
                if link:
                    link.nature_id = move.nature_id
                else:
                    Link.create({'move_id': move.id, 'nature_id': move.nature_id.id})
            elif link:
                link.unlink()

    @api.model_create_multi
    def create(self, vals_list):
        moves = super().create(vals_list)

        # Detect source dynamically (Clinic Patient, POS Order, CSV Import)
        is_clinic = False
        is_online = False
        frame = inspect.currentframe()
        try:
            curr = frame.f_back
            for _ in range(12):
                if not curr:
                    break
                caller_self = curr.f_locals.get('self')
                if isinstance(caller_self, models.BaseModel):
                    model_name = getattr(caller_self, '_name', '')
                    if model_name in ('clinic.patient', 'pos.order'):
                        is_clinic = True
                        break
                    elif model_name == 'import.invoices.wizard':
                        is_online = True
                        break
                curr = curr.f_back
        except Exception:
            pass
        finally:
            del frame

        nature = False
        if is_clinic or self.env.context.get('from_patient_creation'):
            nature = self.env['res.partner.nature'].sudo().search([('code', '=', 'clinic_sales')], limit=1)
        elif is_online or self.env.context.get('from_csv_invoice_import'):
            nature = self.env['res.partner.nature'].sudo().search([('code', '=', 'online_sales')], limit=1)

        if nature:
            Link = self.env['account.move.nature'].sudo()
            for move in moves:
                if not Link.search_count([('move_id', '=', move.id)]):
                    Link.create({'move_id': move.id, 'nature_id': nature.id})

        return moves