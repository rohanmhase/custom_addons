from odoo import models, fields, api
from odoo.osv import expression


class EmiSoftDeleteMixin(models.AbstractModel):
    _name = 'emi.soft.delete.mixin'
    _description = 'EMI Soft Delete Mixin'

    active = fields.Boolean(default=True, string='Active')
    is_purged = fields.Boolean(default=False, string='Purged')

    def unlink(self):
        # IMPORTANT: decide groups BEFORE writing anything
        to_archive = self.filtered(lambda r: r.active and not r.is_purged)
        to_purge = self.filtered(lambda r: (not r.active) and (not r.is_purged))

        if to_archive:
            # 1st delete -> archive only
            to_archive.write({'active': False})

        if to_purge:
            # 2nd delete from archive -> hide from UI, keep in DB
            to_purge.write({
                'active': False,
                'is_purged': True,
            })

        return True

    @api.model
    def _search(self, domain, offset=0, limit=None, order=None, access_rights_uid=None):
        # hide purged always (unless forced)
        if not self.env.context.get('include_purged'):
            domain = expression.AND([domain or [], [('is_purged', '=', False)]])
        return super()._search(
            domain,
            offset=offset,
            limit=limit,
            order=order,
            access_rights_uid=access_rights_uid,
        )