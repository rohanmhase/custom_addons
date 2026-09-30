/** @odoo-module **/

import { KanbanController } from "@web/views/kanban/kanban_controller";
import { patch } from "@web/core/utils/patch";
import { useService } from "@web/core/utils/hooks";
import { Dialog } from "@web/core/dialog/dialog";
import { Component, onMounted, useState, onWillStart } from "@odoo/owl";

export class LeaderboardDialog extends Component {
    static template = "clinic_leaderboard.LeaderboardDialog";
    static components = { Dialog };
    static props = {
        close: Function,
    };

    setup() {
        this.orm = useService("orm");
        this.user = useService("user");

        // Added rsRecords to state to ensure OWL reacts when it populates
        this.state = useState({ records: [], rsRecords: [] });

        onWillStart(async () => {

            // ==========================================
            // 1. LEADERBOARD 1: Daily Records
            // ==========================================
            const fieldsToFetch = [
                "doctor_id", "doctor_name", "clinics_handled",
                "total_pt", "eligible_pt", "recovery_percent", "vas_percent"
            ];

            const topRecords = await this.orm.searchRead(
                "clinic.daily.leaderboard",
                [],
                fieldsToFetch,
                { limit: 1 }
            );

            const userRecords = await this.orm.searchRead(
                "clinic.daily.leaderboard",
                [["doctor_id", "=", this.user.userId]],
                fieldsToFetch,
                { limit: 1 }
            );

            let combinedRecords = [...topRecords];
            if (userRecords.length > 0 && userRecords[0].id !== topRecords[0]?.id) {
                combinedRecords.push(userRecords[0]);
            }
            this.state.records = combinedRecords;

            // ==========================================
            // 2. LEADERBOARD 2: RS Records
            // ==========================================
            const rsFields = ["doctor_id", "doctor_name", "clinics_handled", "total_therapies", "ext_20_40_percent"];

            const topRS = await this.orm.searchRead("clinic.rs.leaderboard", [], rsFields, { limit: 1 });
            const userRS = await this.orm.searchRead("clinic.rs.leaderboard", [["doctor_id", "=", this.user.userId]], rsFields, { limit: 1 });

            let combinedRS = [...topRS];
            const userInRS = combinedRS.some(r => r.id === userRS[0]?.id);
            if (userRS.length > 0 && !userInRS) {
                combinedRS.push(userRS[0]);
            }
            this.state.rsRecords = combinedRS;
        });
    }
}

patch(KanbanController.prototype, {
    setup() {
        super.setup(...arguments);
        this.dialogService = useService("dialog");
        this.user = useService("user");

        onMounted(async () => {
            if (this.props.resModel === "clinic.clinic") {

                const inRestrictedGroup = await this.user.hasGroup("clinic_leaderboard.group_hide_leaderboard");
                const isAdministrator = this.user.isAdmin;

                // Fixed: Changed && to || so it blocks if they are in the group OR if they are an admin
                const isRestricted = inRestrictedGroup && isAdministrator;

                if (!isRestricted) {
                    this.dialogService.add(LeaderboardDialog, {});
                }
            }
        });
    }
});