/** @odoo-module **/

<<<<<<< Updated upstream
import { KanbanRenderer } from "@web/views/kanban/kanban_renderer";
=======
import { Layout } from "@web/search/layout";
>>>>>>> Stashed changes
import { useService } from "@web/core/utils/hooks";
import { Component, onWillStart, useState } from "@odoo/owl";

export class ClinicLeaderboardBanner extends Component {
    static template = "clinic_leaderboard.Banner";

    setup() {
        this.orm = useService("orm");
        this.user = useService("user");

<<<<<<< Updated upstream
        // Define the variables to hold the user's data
=======
>>>>>>> Stashed changes
        this.state = useState({
            show: false,
            dailyRecovery: "0.0",
            ext2040: "0.0",
<<<<<<< Updated upstream
            totalTherapies: 0
        });

        onWillStart(async () => {
            // 1. Check access restrictions (same as popup)
=======
            totalTherapies: 0,
            topDailyText: "N/A",
            topRSText: "N/A",
            departmentName: "" // Added to display their department in the UI
        });

        onWillStart(async () => {
>>>>>>> Stashed changes
            const inRestrictedGroup = await this.user.hasGroup("clinic_leaderboard.group_hide_leaderboard");
            const isAdministrator = this.user.isAdmin;

            if (inRestrictedGroup || isAdministrator) {
<<<<<<< Updated upstream
                return; // Exit and keep 'show' as false
            }

            // 2. Fetch the logged-in user's Daily Record
=======
                return;
            }

            // 1. Fetch current user's employee record to get their department
            const employeeRecord = await this.orm.searchRead(
                "hr.employee",
                [["user_id", "=", this.user.userId]],
                ["department_id"],
                { limit: 1 }
            );

            // Hide banner if user is not linked to an employee profile or has no department
            if (employeeRecord.length === 0 || !employeeRecord[0].department_id) {
                return;
            }

            const deptId = employeeRecord[0].department_id[0];
            const deptName = employeeRecord[0].department_id[1];
            this.state.departmentName = deptName;

            // 2. Check if department is BM, CS, or RS (Uses .includes to catch names like "RS Department")
            const allowedDepartments = ["BM", "CS", "RS"];
            const isAllowed = allowedDepartments.some(d => deptName.includes(d));

            if (!isAllowed) {
                return; // Hide banner for anyone not in BM, CS, or RS
            }

            // 3. Find all users in this specific department to filter the top performers
            const deptEmployees = await this.orm.searchRead(
                "hr.employee",
                [["department_id", "=", deptId]],
                ["user_id"]
            );

            // Extract the user IDs of everyone in their department
            const deptUserIds = deptEmployees
                .map(emp => emp.user_id ? emp.user_id[0] : null)
                .filter(id => id !== null);

            // 4. Fetch the logged-in user's personal records
>>>>>>> Stashed changes
            const daily = await this.orm.searchRead(
                "clinic.daily.leaderboard",
                [["doctor_id", "=", this.user.userId]],
                ["recovery_percent"],
                { limit: 1 }
            );

<<<<<<< Updated upstream
            // 3. Fetch the logged-in user's RS Record
=======
>>>>>>> Stashed changes
            const rs = await this.orm.searchRead(
                "clinic.rs.leaderboard",
                [["doctor_id", "=", this.user.userId]],
                ["ext_20_40_percent", "total_therapies"],
                { limit: 1 }
            );

<<<<<<< Updated upstream
            // 4. Update the state to display the banner
            if (daily.length > 0 || rs.length > 0) {
                this.state.show = true;
=======
            // 5. Fetch the #1 Top Performers specifically for this department
            // Because Metabase inserts data in ranked order, `id asc` grabs the best performer in this filtered group
            const topDaily = await this.orm.searchRead(
                "clinic.daily.leaderboard",
                [["doctor_id", "in", deptUserIds]],
                ["doctor_name", "recovery_percent"],
                { limit: 1, order: "id asc" }
            );

            const topRS = await this.orm.searchRead(
                "clinic.rs.leaderboard",
                [["doctor_id", "in", deptUserIds]],
                ["doctor_name", "ext_20_40_percent"],
                { limit: 1, order: "id asc" }
            );

            // 6. Update the UI state
            if (daily.length > 0 || rs.length > 0) {
                this.state.show = true;

>>>>>>> Stashed changes
                if (daily.length > 0) {
                    this.state.dailyRecovery = daily[0].recovery_percent;
                }
                if (rs.length > 0) {
                    this.state.ext2040 = rs[0].ext_20_40_percent;
                    this.state.totalTherapies = rs[0].total_therapies;
                }
<<<<<<< Updated upstream
            }

            console.log("Current User ID:", this.user.userId);
            console.log("Is Admin?", isAdministrator);
            console.log("Daily records found for this user:", daily.length);
            console.log("RS records found for this user:", rs.length);
            console.log("Will the banner show?", this.state.show);
=======

                if (topDaily.length > 0) {
                    this.state.topDailyText = `${topDaily[0].doctor_name} (${topDaily[0].recovery_percent}%)`;
                }
                if (topRS.length > 0) {
                    this.state.topRSText = `${topRS[0].doctor_name} (${topRS[0].ext_20_40_percent}%)`;
                }
            }
>>>>>>> Stashed changes
        });
    }
}

<<<<<<< Updated upstream
// Inject our custom banner into Odoo's native KanbanRenderer components list
KanbanRenderer.components = {
    ...KanbanRenderer.components,
=======
Layout.components = {
    ...Layout.components,
>>>>>>> Stashed changes
    ClinicLeaderboardBanner,
};