/** @odoo-module **/
console.log("Billing Queue Popup")
import { AbstractAwaitablePopup } from "@point_of_sale/app/popup/abstract_awaitable_popup";
import { useService } from "@web/core/utils/hooks";
import { useState, onWillStart } from "@odoo/owl";
import { usePos } from "@point_of_sale/app/store/pos_hook";
console.log("Import Successful")
export class BillingQueuePopup extends AbstractAwaitablePopup {

    setup() {

        super.setup();

        this.orm = useService("orm");
        this.pos = usePos();

        this.state = useState({records: [], searchTerm: "", });

        onWillStart(async () => {
            const clinicId = this.pos.config.clinic_id?.[0];
            console.log(clinicId)

            this.state.records =
                await this.orm.call(
                    "patient.billing.queue",
                    "get_pending_billing_queue",
                    [clinicId]
                );
            console.log(this.state.records)
        });
    }

    loadRecord(rec) {

        this.props.close({
            confirmed: true,
            record: rec,
        });
    }

    get filteredRecords() {

        const term = (
            this.state.searchTerm || ""
        ).toLowerCase();

        let records = this.state.records;

        // SEARCH
        if (term) {

            records = records.filter(rec =>
                rec.patient_name
                    .toLowerCase()
                    .includes(term)
            );
        }

        // REMOVE DUPLICATES
        const uniqueMap = new Map();

        records.forEach(rec => {

            const key =
                `${rec.queue_type}_${rec.id}`;

            if (!uniqueMap.has(key)) {
                uniqueMap.set(key, rec);
            }
        });

        return Array.from(
            uniqueMap.values()
        );
    }
}

BillingQueuePopup.template =
    "BillingQueuePopupTemplate";

BillingQueuePopup.props = {
    close: Function,
    resolve: Function,
    zIndex: Number,
    id: Number,
    confirmKey: { optional: true },
    cancelKey: { optional: true },
};