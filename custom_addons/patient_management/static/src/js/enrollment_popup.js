/** @odoo-module **/

import { AbstractAwaitablePopup } from "@point_of_sale/app/popup/abstract_awaitable_popup";
import { useService } from "@web/core/utils/hooks";
import { useState, onWillStart } from "@odoo/owl";
import { usePos } from "@point_of_sale/app/store/pos_hook";

export class EnrollmentPopup extends AbstractAwaitablePopup {
    setup() {
        super.setup();

        this.orm = useService("orm");
        this.pos = usePos();

        this.state = useState({ enrollments: [], searchTerm: "", });

        onWillStart(async () => {
            const clinicId = this.pos.config.clinic_id?.[0];

            this.state.enrollments = await this.orm.call(
                "patient.enrollment",
                "get_pending_enrollments",
                [clinicId]
            );
        });
    }

    selectEnrollment(rec) {
        this.props.close({
            confirmed: true,
            record: rec,
        });
    }

    get filteredEnrollments() {

        const term = (
            this.state.searchTerm || ""
        ).toLowerCase();

        let records = this.state.enrollments;

        if (term) {
            records = records.filter(rec =>
                rec.patient_name.toLowerCase().includes(term)
            );
        }

        const uniqueMap = new Map();

        records.forEach(rec => {
            if (!uniqueMap.has(rec.id)) {
                uniqueMap.set(rec.id, rec);
            }
        });

        return Array.from(uniqueMap.values());
    }
}

EnrollmentPopup.template = "EnrollmentPopupTemplate";

EnrollmentPopup.props = {
    close: Function,
    resolve: Function,
    zIndex: Number,
    id: Number,
    confirmKey: { optional: true },
    cancelKey: { optional: true },
};