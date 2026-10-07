/** @odoo-module **/

import { KanbanRenderer } from "@web/views/kanban/kanban_renderer";
import { useService } from "@web/core/utils/hooks";
import { Component, onWillStart, useState } from "@odoo/owl";

export class ClinicLeaderboardBanner extends Component {
    static template = "clinic_leaderboard.Banner";

    setup() {
        this.orm = useService("orm");
        this.user = useService("user");

        // Define the variables to hold the user's data
        this.state = useState({
            show: false,
            dailyRecovery: "0.0",
            ext2040: "0.0",
            totalTherapies: 0
        });

        onWillStart(async () => {
            // 1. Check access restrictions (same as popup)
            const inRestrictedGroup = await this.user.hasGroup("clinic_leaderboard.group_hide_leaderboard");
            const isAdministrator = this.user.isAdmin;

            if (inRestrictedGroup || isAdministrator) {
                return; // Exit and keep 'show' as false
            }

            // 2. Fetch the logged-in user's Daily Record
            const daily = await this.orm.searchRead(
                "clinic.daily.leaderboard",
                [["doctor_id", "=", this.user.userId]],
                ["recovery_percent"],
                { limit: 1 }
            );

            // 3. Fetch the logged-in user's RS Record
            const rs = await this.orm.searchRead(
                "clinic.rs.leaderboard",
                [["doctor_id", "=", this.user.userId]],
                ["ext_20_40_percent", "total_therapies"],
                { limit: 1 }
            );

            // 4. Update the state to display the banner
            if (daily.length > 0 || rs.length > 0) {
                this.state.show = true;
                if (daily.length > 0) {
                    this.state.dailyRecovery = daily[0].recovery_percent;
                }
                if (rs.length > 0) {
                    this.state.ext2040 = rs[0].ext_20_40_percent;
                    this.state.totalTherapies = rs[0].total_therapies;
                }
            }

            console.log("Current User ID:", this.user.userId);
            console.log("Is Admin?", isAdministrator);
            console.log("Daily records found for this user:", daily.length);
            console.log("RS records found for this user:", rs.length);
            console.log("Will the banner show?", this.state.show);
        });
    }
}

// Inject our custom banner into Odoo's native KanbanRenderer components list
KanbanRenderer.components = {
    ...KanbanRenderer.components,
    ClinicLeaderboardBanner,
};