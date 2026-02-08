from odoo import models, fields, api, _
from odoo.exceptions import UserError
from datetime import datetime, timedelta


class Prescription(models.Model):
    _name = "patient.prescription"
    _description = "Patient Prescription"

    patient_id = fields.Many2one("clinic.patient", string="Patient", required=True, readonly=True)
    doctor_id = fields.Many2one("res.users", string="Doctor",
                                required=True,
                                default=lambda self: self.env.user,
                                readonly=True)
    prescription_date = fields.Date(string="Prescription Date",
                                    default=lambda self: self._ist_date(),
                                    readonly=True)
    clinic_id = fields.Many2one(
        "clinic.clinic",
        string="Clinic",
        required=True,
        related="patient_id.clinic_id"
    )
    line_ids = fields.One2many("patient.prescription.line",
                               "prescription_id",
                               string="Medicines",
                               required=True)

    notes = fields.Char(string="Notes")

    state = fields.Selection(
        [
            ("draft", "Draft"),
            ("confirmed", "Confirmed"),
            ("done", "Done"),
        ],
        string="Status",
        default="draft",
    )

    latest_followup_id = fields.Many2one(
        'patient.followup',
        string='Latest Follow Up',
        compute='_compute_latest_followup',
        store=False
    )

    latest_blood_report_id = fields.Many2one(
        'patient.blood_report',
        string='Latest Blood Report',
        compute='_compute_latest_blood_report',
        store=False
    )

    @api.depends('patient_id')
    def _compute_latest_followup(self):
        Followup = self.env['patient.followup']
        for rec in self:
            if rec.patient_id:
                rec.latest_followup_id = Followup.search(
                    [('patient_id', '=', rec.patient_id.id), ('active', '=', 'true')],
                    order='weekly_followup_date desc',
                    limit=1
                )
            else:
                rec.latest_followup_id = False

    @api.depends('patient_id')
    def _compute_latest_blood_report(self):
        Blood_Report = self.env['patient.blood_report']
        for rec in self:
            if rec.patient_id:
                rec.latest_blood_report_id = Blood_Report.search(
                    [('patient_id', '=', rec.patient_id.id), ('active', '=', 'true')],
                    order='blood_report_date desc',
                    limit=1
                )
            else:
                rec.latest_blood_report_id = False

    active = fields.Boolean(default=True)


    def _check_has_lines(self):
        """Raise error if no lines exist."""
        for rec in self:
            if not rec.line_ids:
                raise UserError(_("⚠️ You cannot update a prescription without medicines."))


    @api.model
    def get_available_medicines(self, clinic_id):
        """Get all available medicines with stock for the clinic's warehouse"""
        clinic = self.env['clinic.clinic'].browse(clinic_id)
        if not clinic or not clinic.warehouse_id:
            return []

        warehouse = clinic.warehouse_id
        products = self.env['product.product'].search([
            ('type', '=', 'product'),
            ('sale_ok', '=', True),
            ('active', '=', True)
        ])

        medicine_data = []
        for product in products:
            qty_available = product.with_context(
                location=warehouse.lot_stock_id.id
            ).qty_available if warehouse.lot_stock_id else product.qty_available

            medicine_data.append({
                'id': product.id,
                'name': product.display_name,
                'qty_available': qty_available,
                'image': product.image_128,
            })

        return medicine_data

    def _check_stock(self):
        """Check all lines against available stock"""
        self._check_has_lines()
        error_msgs = []
        for line in self.line_ids:
            product = line.product_id
            if product.type != "product":
                continue
            warehouse = self.clinic_id.warehouse_id
            qty_available = product.with_context(
                location=warehouse.lot_stock_id.id
            ).qty_available if warehouse and warehouse.lot_stock_id else product.qty_available

            if line.qty > qty_available:
                error_msgs.append(
                    _("❌ %s is out of stock. Available: %s, Required: %s")
                    % (product.display_name, qty_available, line.qty)
                )

        if error_msgs:
            raise UserError("\n".join(error_msgs))

    def _notify_pos_prescription_created(self):
        """Send real-time notification to active POS sessions when a prescription is confirmed."""
        bus = self.env['bus.bus']
        channel = f"pos_prescription_notification_{self.clinic_id.id}"
        message_data = {
            'prescription_id': self.id,
            'patient_id': self.patient_id.id,
            'partner_id': self.patient_id.partner_id.id,
            'patient_name': self.patient_id.name,
            'doctor_name': self.doctor_id.name,
            'clinic_id': self.clinic_id.id,
            'clinic_name': self.clinic_id.name,
            'line_count': len(self.line_ids),
            'prescription_date': self.prescription_date.strftime('%Y-%m-%d') if self.prescription_date else False,
            'message': _("🩺 New Prescription for %s by Dr. %s") % (self.patient_id.name, self.doctor_id.name),
        }

        # ✅ Find active POS sessions (cashiers currently logged in)
        domain = [('state', '=', 'opened')]
        if self.clinic_id:
            domain.append(('config_id.clinic_id', '=', self.clinic_id.id))

        print(domain)
        active_sessions = self.env['pos.session'].search(domain)

        # ✅ Extract the user (cashier) from each session
        pos_users = active_sessions.mapped('user_id')

        # print(pos_users)
        # if not pos_users:
        #     # Fallback: notify all POS users in case there are no open sessions
        #     pos_users = self.env['res.users'].search([
        #         ('groups_id', 'in', self.env.ref('point_of_sale.group_pos_user').id)
        #     ])

        # ✅ Send message to each POS user's partner channel
        for user in pos_users:
            print(user.partner_id)
            print(message_data)
            if user.partner_id:
                bus._sendone(
                    user.partner_id,
                    channel,
                    message_data
                )

    # ------------------ OVERRIDE CREATE ------------------ #
    @api.model
    def create(self, vals):
        if not vals.get('line_ids'):
            raise UserError(_("⚠️ You cannot create a prescription without medicines."))
        record = super().create(vals)
        # Check stock immediately after creation
        record._check_stock()
        return record

    # ------------------ CONFIRM ACTION ------------------ #
    def action_confirm(self):
        for rec in self:
            rec._check_has_lines()
            if rec.state == "done":
                raise UserError(_("⚠️ You cannot reconfirm a prescription that is already %s.") % rec.state)
            if not rec.line_ids:
                raise UserError(_("⚠️ You cannot confirm a prescription without medicines."))

            rec._check_stock()  # Reuse same stock validation
            rec.state = "confirmed"
            rec._notify_pos_prescription_created()

        return {
            'type': 'ir.actions.client',
            'tag': 'display_notification',
            'params': {
                'title': _('✅ Success'),
                'message': _('Prescription confirmed successfully!'),
                'type': 'success',
                'sticky': False,
            }
        }

    # ------------------ OVERRIDE WRITE ------------------ #
    def write(self, vals):
        for rec in self:
            if rec.state == "done":
                raise UserError(_("⚠️ You cannot update a prescription that is Done."))
        result = super().write(vals)

        for rec in self:
            if rec.state == "confirmed":
                rec._check_stock()  # Ensure stock still available after edit

        return result

    def copy(self, default=None):
        raise UserError(_("⚠️ Duplication of this record is not allowed."))

    @api.model
    def get_latest_prescription(self, patient_id):
        """Return latest confirmed prescription lines for given patient (customer)"""
        patient = self.env["clinic.patient"].search(
            [("partner_id", "=", patient_id)], limit=1
        )
        prescription = self.search(
            [("patient_id", "=", patient.id), ("state", "=", "confirmed")],
            order="id desc",
            limit=1,
        )
        if not prescription:
            return []

        return [
            {
                "id": line.product_id.id,
                "qty": line.qty,
                "name": line.product_id.display_name,
            }
            for line in prescription.line_ids
        ]

    def _ist_date(self):
        utc = (datetime.now())
        td = timedelta(hours=5, minutes=30)
        ist_date = utc + td
        return ist_date.date()

    def unlink(self):
        for record in self:
            record.active = False
        # Do not call super() → prevents actual deletion
        return True

    def action_print_prescription(self):
        """Print Prescription PDF"""
        if self.state == "draft":
            raise UserError(_("⚠️ Please confirm the prescription before printing."))

        return self.env.ref('patient_management.report_prescription').report_action(self)

class PrescriptionLine(models.Model):
    _name = "patient.prescription.line"
    _description = "Prescription Line"

    prescription_id = fields.Many2one(
        "patient.prescription", string="Prescription", required=True)
    product_id = fields.Many2one("product.product", string="Medicine", required=True)
    qty = fields.Float(string="Quantity", default=1.0)
    instructions = fields.Selection([('before_food', 'Before Food'),
                                     ('after_food', 'After Food'),
                                     ('at_night_time', 'At Night Time'),
                                     ('along_with_milk', 'Along With Milk'),
                                     ('along_with_milk_rock_sugar', 'Along With Milk + Rock Sugar'),
                                     ('along_with_lukewarm_water', 'Along With Lukewarm Water'),
                                     ('10am_6pm', '10 am - 6 pm'),
                                     ('8am_5pm', '8 am - 5 pm'),
                                     ('local_application', 'Local Application'),
                                     ], string="Instructions")
    dosage = fields.Selection([('2 - 0 - 2', '2 - 0 - 2'),
                               ('2 - 2 - 2', '2 - 2 - 2'),
                               ('3 - 0 - 3', '3 - 0 - 3'),
                               ('3 - 3 - 3', '3 - 3 - 3'),
                               ('1 - 0 - 1', '1 - 0 - 1'),
                               ('1 - 1 - 1', '1 - 1 - 1'),
                               ('0 - 1 - 0', '0 - 1 - 0'),
                               ('0 - 2 - 0', '0 - 2 - 0'),
                               ('0 - 0 - 1', '0 - 0 - 1'),
                               ('0 - 0 - 2', '0 - 0 - 2'),
                               ('0 - 0 - 3', '0 - 0 - 3'),
                               ('0 - 0 - 4', '0 - 0 - 4'), ],
                              string="Dosage")
    qty_available = fields.Float(
        string="Available Qty", compute="_compute_qty_available", readonly=True
    )

    active = fields.Boolean(default=True)

    @api.depends("product_id", "prescription_id.clinic_id")
    def _compute_qty_available(self):
        for line in self:
            qty = 0.0
            if line.product_id and line.prescription_id.clinic_id:
                warehouse = line.prescription_id.clinic_id.warehouse_id
                if warehouse and warehouse.lot_stock_id:
                    qty = line.product_id.with_context(
                        location=warehouse.lot_stock_id.id
                    ).qty_available
            line.qty_available = qty

