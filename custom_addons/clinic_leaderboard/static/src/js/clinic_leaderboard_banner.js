/** @odoo-module **/

import { Layout } from "@web/search/layout";
import { useService } from "@web/core/utils/hooks";
import { Component, onWillStart, useState } from "@odoo/owl";

function getTrend(current, previous) {
    if (current > previous) return { icon: "▲", color: "text-bg-success" };
    if (current < previous) return { icon: "▼", color: "text-bg-danger" };
    return { icon: "", color: "text-bg-warning" };
}

export class ClinicLeaderboardBanner extends Component {
    static template = "clinic_leaderboard.Banner";

    setup() {
        this.orm = useService("orm");
        this.user = useService("user");

        this.state = useState({
            show: false,
            departmentName: "",
            myMetrics: [],
            topPerformers: []
        });

        onWillStart(async () => {
            const inRestrictedGroup = await this.user.hasGroup("clinic_leaderboard.group_hide_leaderboard");
//            const isAdministrator = this.user.isAdmin;

//            if (inRestrictedGroup || isAdministrator) {
//                return;
//            }

            const employeeRecord = await this.orm.searchRead(
                "hr.employee",
                [["user_id", "=", this.user.userId]],
                ["department_id"],
                { limit: 1 }
            );

            if (employeeRecord.length === 0 || !employeeRecord[0].department_id) {
                return;
            }

            const deptId = employeeRecord[0].department_id[0];
            const deptName = employeeRecord[0].department_id[1];
            this.state.departmentName = deptName;

            let deptCode = "";
            if (deptName.includes("RS")) deptCode = "RS";
            else if (deptName.includes("BM")) deptCode = "BM";
            else if (deptName.includes("CS")) deptCode = "CS";

            if (!deptCode) return;

            const deptEmployees = await this.orm.searchRead(
                "hr.employee",
                [["department_id", "=", deptId]],
                ["user_id"]
            );

            const deptUserIds = deptEmployees
                .map(emp => emp.user_id ? emp.user_id[0] : null)
                .filter(id => id !== null);

            let myMetrics = [];
            let topPerformers = [];

            if (deptCode === "RS") {
                // 1. Fetch personal metrics
                const metricsUser = await this.orm.searchRead(
                    "clinic.leaderboard.metrics.user",
                    [["doctor_id", "=", this.user.userId]],
                    ["recovery_percent", "prev_recovery_percent", "vas_percent"],
                    { limit: 1 }
                );

                // 2. Fetch personal extensions
                const extUser = await this.orm.searchRead(
                    "clinic.leaderboard.extension.user",
                    [["doctor_id", "=", this.user.userId]],
                    ["extension_percent", "prev_extension_percent"],
                    { limit: 1 }
                );

                // 3. Fetch Top 3 Metrics
                const topMetrics = await this.orm.searchRead(
                    "clinic.leaderboard.metrics.topper",
                    [],
                    ["doctor_name", "score", "category"]
                );

                // 4. Fetch Top Extension
                const topExt = await this.orm.searchRead(
                    "clinic.leaderboard.extension.topper",
                    [],
                    ["doctor_name", "score", "category"]
                );

                // Populate Personal MyMetrics Badges
                if (metricsUser.length > 0) {
                    const rec = metricsUser[0].recovery_percent;
                    const prevRec = metricsUser[0].prev_recovery_percent;
                    const recTrend = getTrend(rec, prevRec);

                    myMetrics.push({ label: "Recovery", value: `${rec}%`, color: recTrend.color, icon: recTrend.icon });
                    myMetrics.push({ label: "VAS", value: `${metricsUser[0].vas_percent}%`, color: "text-bg-info", icon: "▬" });
                }

                if (extUser.length > 0) {
                    const ext = extUser[0].extension_percent;
                    const prevExt = extUser[0].prev_extension_percent;
                    const extTrend = getTrend(ext, prevExt);

                    myMetrics.push({ label: "20-40% Ext", value: `${ext}%`, color: extTrend.color, icon: extTrend.icon });
                }

                // Populate Top Performers Marquee
                const allToppers = [...topMetrics, ...topExt];
                if (allToppers.length > 0) {
                    allToppers.forEach(topper => {
                        topPerformers.push(`🥇 Top ${topper.category}: ${topper.doctor_name} (${topper.score})`);
                    });
                }
            }

            if (myMetrics.length > 0 || topPerformers.length > 0) {
                this.state.show = true;
                this.state.myMetrics = myMetrics;
                this.state.topPerformers = topPerformers;
            }
        });
    }
}

Layout.components = {
    ...Layout.components,
    ClinicLeaderboardBanner,
};