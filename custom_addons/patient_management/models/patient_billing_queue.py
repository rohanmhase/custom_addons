from odoo import models, api
from datetime import datetime


class PatientBillingQueue(models.AbstractModel):
    _name = 'patient.billing.queue'
    _description = 'Patient Billing Queue'

    @api.model
    def get_pending_billing_queue(self, clinic_id):

        result = []

        # PRESCRIPTIONS
        prescriptions = self.env[
            'patient.prescription'
        ].get_pending_prescriptions(clinic_id)

        for rec in prescriptions:

            rec['queue_type'] = 'prescription'

            result.append(rec)

        # ENROLLMENTS
        enrollments = self.env[
            'patient.enrollment'
        ].get_pending_enrollments(clinic_id)

        for rec in enrollments:

            rec['queue_type'] = 'enrollment'

            result.append(rec)

        # SORT BY DATE DESC
        result = sorted(
            result,
            key=lambda x: datetime.strptime(
                x.get('date', '01-01-1900'),
                '%d-%m-%Y'
            ),
            reverse=True
        )

        return result