import re

from odoo import api, fields, models, _
from odoo.exceptions import ValidationError

def _normalize_mobile(value):
    """Return a comparable 10-digit Indian mobile number where possible."""
    digits = re.sub(r"\D", "", value or "")
    if len(digits) == 12 and digits.startswith("91"):
        digits = digits[2:]
    elif len(digits) == 11 and digits.startswith("0"):
        digits = digits[1:]
    return digits

class PatientReference(models.Model):
    _name = "patient.reference"
    _description = "Patient Reference"
    _order = "create_date desc, id desc"

    patient_id = fields.Many2one(
        "clinic.patient",
        string="Referred By",
        required=True,
        ondelete="cascade",
        index=True,
    )

    name = fields.Char(
        string="Name",
        required=True,
    )

    phone = fields.Char(
        string="Mobile Number",
        required=True,
        size=10,
        index=True,
    )

    is_joined = fields.Boolean(
        string="Joined",
        default=False,
        readonly=True,
        index=True,
        help="Automatically marked when a registered patient is found with this mobile number.",
    )

    active = fields.Boolean(default=True)

    @api.model
    def _registered_patient_exists(self, phone):
        normalized_phone = _normalize_mobile(phone)
        if len(normalized_phone) != 10:
            return False

        # Check globally because a number already registered in any clinic/company
        # must not become a fresh referral again. Normalize stored patient phones
        # in SQL so formats such as +91 98765 43210 still match 9876543210.
        self.env.cr.execute(
            """
            SELECT 1
              FROM clinic_patient
             WHERE RIGHT(
                       REGEXP_REPLACE(COALESCE(phone, ''), '[^0-9]', '', 'g'),
                       10
                   ) = %s
             LIMIT 1
            """,
            (normalized_phone,),
        )
        return bool(self.env.cr.fetchone())

    @api.model_create_multi
    def create(self, vals_list):
        for vals in vals_list:
            if "phone" in vals:
                vals["phone"] = _normalize_mobile(vals.get("phone"))

            phone = vals.get("phone")
            if phone and self._registered_patient_exists(phone):
                raise ValidationError(_(
                    "This mobile number already belongs to a registered patient and cannot be added as a referral."
                ))

        return super().create(vals_list)

    def write(self, vals):
        vals = dict(vals)

        # Once a referred person has joined, lock the referral attribution.
        # This prevents changing the phone/referrer and corrupting referral history.
        for record in self:
            if record.is_joined:
                if "phone" in vals:
                    new_phone = _normalize_mobile(vals.get("phone"))
                    if new_phone != record.phone:
                        raise ValidationError(_(
                            "Mobile number cannot be changed after the referred patient has joined."
                        ))


        if "phone" in vals:
            vals["phone"] = _normalize_mobile(vals.get("phone"))
            if vals["phone"] and self._registered_patient_exists(vals["phone"]):
                raise ValidationError(_(
                    "This mobile number already belongs to a registered patient and cannot be used as a referral."
                ))

        return super().write(vals)

    @api.constrains("phone")
    def _check_phone(self):
        for record in self:
            if record.phone and not record.phone.isdigit():
                raise ValidationError(_("Mobile number must contain digits only."))
            if record.phone and len(record.phone) != 10:
                raise ValidationError(_("Mobile number must contain exactly 10 digits."))

    def unlink(self):
        for record in self:
            record.active = False
        # Do not call super() → prevents actual deletion
        return True