/** @odoo-module **/

import { Component } from "@odoo/owl";

export class PaymentSummaryPopup extends Component {
    setup() {
        super.setup();
    }

    confirm() {
        // Force the popup to close and return confirmed: true
        this.props.close({ confirmed: true, payload: null });
    }

    cancel() {
        // Force the popup to close and return confirmed: false
        this.props.close({ confirmed: false, payload: null });
    }
}

PaymentSummaryPopup.template = "PaymentSummaryPopup";