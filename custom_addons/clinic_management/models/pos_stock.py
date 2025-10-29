from odoo import api, fields, models, _
from odoo.exceptions import UserError, ValidationError

class StockWarehouse(models.Model):
    _inherit = "stock.warehouse"
    clinic_id = fields.Many2one("clinic.clinic", string="Clinic")

class PosConfig(models.Model):
    _inherit = "pos.config"
    clinic_id = fields.Many2one("clinic.clinic", string="Clinic")

    def get_limited_partners_loading(self):
        """Override to filter partner IDs by clinic"""
        result = super().get_limited_partners_loading()

        # Get the original partner IDs (list of tuples [(id,), (id,), ...])
        partner_ids = [res[0] for res in result] if result else []

        if partner_ids:
            if self.clinic_id:
                # Search partners that are in the list AND belong to this clinic
                filtered_partners = self.env['res.partner'].search([
                    ('id', 'in', partner_ids),
                    ('clinic_id', '=', self.clinic_id.id)
                ])
            else:
                # For main POS: only show partners WITHOUT any clinic
                filtered_partners = self.env['res.partner'].search([
                    ('id', 'in', partner_ids),
                    ('clinic_id', '=', False)
                ])

            result = [(partner.id,) for partner in filtered_partners]

        return result


class PosSession(models.Model):
    _inherit = 'pos.session'

    def _get_partners_domain(self):
        """Override to add clinic filter"""
        domain = super()._get_partners_domain()

        # Add clinic filter if POS has a clinic assigned
        if self.config_id.clinic_id:
            domain.append(('clinic_id', '=', self.config_id.clinic_id.id))
        else:
            # For main POS: only partners without clinic
            domain.append(('clinic_id', '=', False))
        return domain


    def _loader_params_res_partner(self):
        result = super()._loader_params_res_partner()

        # Add clinic_id to the fields that will be loaded
        if 'clinic_id' not in result['search_params']['fields']:
            result['search_params']['fields'].append('clinic_id')
        # Add clinic filter to the domain
        if 'domain' not in result['search_params']:
            result['search_params']['domain'] = []

        if self.config_id.clinic_id:
            # For clinic POS: filter by clinic
            result['search_params']['domain'].append(('clinic_id', '=', self.config_id.clinic_id.id))
        else:
            # For main POS: only partners without clinic
            result['search_params']['domain'].append(('clinic_id', '=', False))
        return result

class PosOrder(models.Model):
    _inherit = 'pos.order'

    @api.constrains('partner_id')
    def check_partner_id(self):
        """Ensure customer is selected before validating order"""
        for order in self:
            if not order.partner_id:
                raise ValidationError(_('Please select the customer before validating the order'))


    def _process_order(self, order, draft, existing_order):

        """Override to add customer validation"""
        if not order.get('data', {}).get('partner_id'):
            raise UserError(_('Customer is required. Please select a customer before validating the order'))

        if'l10n_es_tbai_refund_reason' in order['data']:
            return super(PosOrder, self.with_context(l10n_es_tbai_refund_reason=order['data']['l10n_es_tbai_refund_reason']))._process_order(order, draft, existing_order)
        else:
            return super()._process_order(order, draft, existing_order)