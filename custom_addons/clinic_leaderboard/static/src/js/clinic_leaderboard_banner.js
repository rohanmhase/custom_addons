/** @odoo-module **/

import { Layout } from "@web/search/layout";
import { registry } from "@web/core/registry";
import { useService } from "@web/core/utils/hooks";
import { Component, onMounted, onWillUnmount, useState } from "@odoo/owl";

// Metabase is imported daily. Keep the small dashboard response in memory while
// users navigate between Odoo views. Each browser tab performs at most one RPC
// per TTL (and concurrent Layout instances share the same in-flight request).
const CACHE_TTL_MS = 15 * 60 * 1000;
const ERROR_RETRY_MS = 30 * 1000;

const leaderboardDataService = {
    dependencies: ["orm"],
    async: ["getData"],
    start(_env, { orm }) {
        let cachedData = null;
        let expiresAt = 0;
        let pendingRequest = null;
        let retryAfter = 0;

        return {
            getData() {
                const now = Date.now();
                if (cachedData !== null && now < expiresAt) {
                    return Promise.resolve(cachedData);
                }
                if (pendingRequest) {
                    return pendingRequest;
                }
                if (now < retryAfter) {
                    // Preserve the last good result if the server is temporarily busy.
                    return Promise.resolve(cachedData);
                }

                pendingRequest = orm.call(
                    "clinic.leaderboard.metrics.user",
                    "get_dashboard_data",
                    []
                ).then((data) => {
                    cachedData = data;
                    expiresAt = Date.now() + CACHE_TTL_MS;
                    retryAfter = 0;
                    return data;
                }).catch((error) => {
                    retryAfter = Date.now() + ERROR_RETRY_MS;
                    console.warn("Clinic leaderboard data could not be loaded", error);
                    return cachedData;
                }).finally(() => {
                    pendingRequest = null;
                });
                return pendingRequest;
            },
        };
    },
};

registry.category("services").add("clinic_leaderboard_data", leaderboardDataService);

function getTrend(current, previous) {
    if (current > previous) return { icon: "▲", color: "text-bg-success" };
    if (current < previous) return { icon: "▼", color: "text-bg-danger" };
    return { icon: "", color: "text-bg-warning" };
}

// Added for metrics where a lower score is better (e.g., Only Consultation)
function getReverseTrend(current, previous) {
    if (current < previous) return { icon: "▼", color: "text-bg-success" };
    if (current > previous) return { icon: "▲", color: "text-bg-danger" };
    return { icon: "", color: "text-bg-warning" };
}

export class ClinicLeaderboardBanner extends Component {
    static template = "clinic_leaderboard.Banner";

    setup() {
        this.leaderboardData = useService("clinic_leaderboard_data");
        this.state = useState({
            show: false,
            departmentName: "",
            myMetrics: [],
            topPerformers: [],
        });
        this._mounted = false;

        // Unlike onWillStart, onMounted never delays the first page render.
        onMounted(() => {
            this._mounted = true;
            void this.loadLeaderboard();
        });
        onWillUnmount(() => {
            this._mounted = false;
        });
    }

    async loadLeaderboard() {
        const data = await this.leaderboardData.getData();
        if (!this._mounted || !data) {
            return;
        }

        const myMetrics = [];
        const topPerformers = [];
        const userMetrics = data.metricsUser;
        const extUser = data.extUser;

        if (userMetrics) {
            // Logic for RS Department Metrics (Checks for RS-specific field)
            if (userMetrics.recovery_percent !== undefined) {
                const recTrend = getTrend(userMetrics.recovery_percent, userMetrics.prev_recovery_percent);
                myMetrics.push({
                    label: "Recovery", value: `${userMetrics.recovery_percent}%`,
                    color: recTrend.color, icon: recTrend.icon,
                });

                const vasTrend = getTrend(userMetrics.vas_percent, userMetrics.prev_total_vas);
                myMetrics.push({
                    label: "VAS", value: `${userMetrics.vas_percent}%`,
                    color: vasTrend.color, icon: vasTrend.icon,
                });

                const therapiesTrend = getTrend(userMetrics.total_therapies, userMetrics.prev_total_therapies);
                myMetrics.push({
                    label: "Therapies", value: `${userMetrics.total_therapies}`,
                    color: therapiesTrend.color, icon: therapiesTrend.icon,
                });
            }

            // Logic for CS Department Metrics
            if (data.departmentName === "CS" && userMetrics.ppc_amount !== undefined) {
                const ppcTrend = getTrend(userMetrics.ppc_amount, userMetrics.prev_ppc_amount);
                myMetrics.push({
                    label: "PPC", value: `${userMetrics.ppc_amount}`,
                    color: ppcTrend.color, icon: ppcTrend.icon,
                });

                const convTrend = getTrend(userMetrics.conversion_percent, userMetrics.prev_conversion_percent);
                myMetrics.push({
                    label: "Conversion", value: `${userMetrics.conversion_percent}%`,
                    color: convTrend.color, icon: convTrend.icon,
                });

                const sameDayTrend = getTrend(userMetrics.same_day_conversion, userMetrics.prev_same_day_conversion);
                myMetrics.push({
                    label: "Same Day Conversion", value: `${userMetrics.same_day_conversion}`,
                    color: sameDayTrend.color, icon: sameDayTrend.icon,
                });

                const consultTrend = getReverseTrend(userMetrics.only_consultation, userMetrics.prev_only_consultation);
                myMetrics.push({
                    label: "Only Consultation", value: `${userMetrics.only_consultation}`,
                    color: consultTrend.color, icon: consultTrend.icon,
                });
            }

            // Logic for BM Department Metrics
            if (data.departmentName === "BM" && userMetrics.npc !== undefined) {
                const ppcTrend = getTrend(userMetrics.ppc_amount, userMetrics.prev_ppc_amount);
                myMetrics.push({
                    label: "PPC", value: `${userMetrics.ppc_amount}`,
                    color: ppcTrend.color, icon: ppcTrend.icon,
                });

                const convTrend = getTrend(userMetrics.conversion_percent, userMetrics.prev_conversion_percent);
                myMetrics.push({
                    label: "Conversion", value: `${userMetrics.conversion_percent}%`,
                    color: convTrend.color, icon: convTrend.icon,
                });

                const npcTrend = getTrend(userMetrics.npc, userMetrics.prev_npc);
                myMetrics.push({
                    label: "NPC", value: `${userMetrics.npc}`,
                    color: npcTrend.color, icon: npcTrend.icon,
                });

                const refTrend = getTrend(userMetrics.referral_visits, userMetrics.prev_referral_visits);
                myMetrics.push({
                    label: "Referrals", value: `${userMetrics.referral_visits}`,
                    color: refTrend.color, icon: refTrend.icon,
                });

                const pcTrend = getTrend(userMetrics.pc_p_joined, userMetrics.prev_pc_p_joined);
                myMetrics.push({
                    label: "PC+P1", value: `${userMetrics.pc_p_joined}`,
                    color: pcTrend.color, icon: pcTrend.icon,
                });
            }
        }

        if (extUser) {
            const extTrend = getTrend(extUser.extension_percent, extUser.prev_extension_percent);
            myMetrics.push({
                label: "20-40% Ext", value: `${extUser.extension_percent}%`,
                color: extTrend.color, icon: extTrend.icon,
            });
        }

        for (const topper of [...(data.topMetrics || []), ...(data.topExt || [])]) {
            topPerformers.push(`🥇 ${topper.category}: ${topper.doctor_name} (${topper.score})`);
        }

        this.state.departmentName = data.departmentName || "";
        this.state.myMetrics = myMetrics;
        this.state.topPerformers = topPerformers;
        this.state.show = myMetrics.length > 0 || topPerformers.length > 0;
    }
}

Layout.components = {
    ...Layout.components,
    ClinicLeaderboardBanner,
};