from odoo import models, fields, api

class HrEmployee(models.Model):
    _inherit = 'hr.employee'

    def write(self, vals):

        res = super(HrEmployee, self).write(vals)
        if 'active' in vals:

            new_state =vals.get('active')
            for record in self:
                if record.user_id:
                    record.user_id.active = new_state


        return res

    def unlink(self):
        for record in self:
            if record.user_id:
                record.user_id.active = False
        return super(HrEmployee, self).unlink()