/** @odoo-module **/

import { registry } from "@web/core/registry";
import { listView } from "@web/views/list/list_view";
import { ListController } from "@web/views/list/list_controller";
import { useService } from "@web/core/utils/hooks";

export class ClinicStockConfirmationListController extends ListController {
    setup() {
        super.setup();
        this.orm = useService("orm");
    }

    async onClickConfirmAll() {
        await this.orm.call(
            "clinic.stock.confirmation",
            "action_confirm_all",
            [[]],
            { context: this.props.context }
        );
        await this.model.load();
    }
}

export const clinicStockConfirmationListView = {
    ...listView,
    Controller: ClinicStockConfirmationListController,
    buttonTemplate: "clinic_stock_confirmation.ListView.Buttons",
};

registry.category("views").add("clinic_stock_confirmation_list", clinicStockConfirmationListView);

// Internal Transfer Confirmation Controller
export class ClinicTransferConfirmationListController extends ListController {
    setup() {
        super.setup();
        this.orm = useService("orm");
    }

    async onClickConfirmAll() {
        await this.orm.call(
            "clinic.internal.transfer.confirmation",
            "action_confirm_all",
            [[]],
            { context: this.props.context }
        );
        await this.model.load();
    }
}

export const clinicTransferConfirmationListView = {
    ...listView,
    Controller: ClinicTransferConfirmationListController,
    buttonTemplate: "clinic_stock_confirmation.ListView.Buttons",
};

registry.category("views").add("clinic_transfer_confirmation_list", clinicTransferConfirmationListView);