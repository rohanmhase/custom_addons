/** @odoo-module **/

import { Component } from "@odoo/owl";
import { registry } from "@web/core/registry";
import { useService } from "@web/core/utils/hooks";

export const prescriptionNotificationService = {
    dependencies: ["bus_service", "notification", "pos"],

    start(env, { bus_service, notification }) {
        console.log("🚀 Prescription Notification Service Started");

        const self = this;
        self.env = env;
        self.notification = notification;

        // ✅ Get POS Current Clinic ID
        const pos = env.services.pos;
        const clinicId = pos?.config?.clinic_id?.[0];

        if (!clinicId) {
            console.warn("⚠️ No clinic ID found in POS config. Notifications disabled.");
            return;
        }

        const channel = `pos_prescription_notification_${clinicId}`;
        console.log("✅ Clinic Notification Channel:", channel);

        // ✅ Restore pending notifications (per clinic)
//        self.restorePendingNotifications();

        // ✅ Subscribe only to this clinic's channel
        bus_service.addChannel(channel);

        bus_service.addEventListener("notification", ({ detail: notifications }) => {
            for (const { type, payload } of notifications) {
                if (type === channel) {
                    self.handlePrescriptionNotification(payload, notification);
                }
            }
        });

        console.log("✅ Subscribed only to:", channel);
    },

    handlePrescriptionNotification(data, notification) {
        console.log("🩺 handlePrescriptionNotification called");

        const message = data.message || `New Prescription for ${data.patient_name}`;
        const details = [
            `Patient: ${data.patient_name}`,
            `Doctor: ${data.doctor_name}`,
            `Clinic: ${data.clinic_name}`,
            `Date: ${data.prescription_date || "Today"}`,
        ].join("\n");

        // ✅ Save per clinic
//        this.savePendingNotification(data);

        notification.add(message, {
            title: "🩺 New Prescription",
            type: "info",
            sticky: true,
            buttons: [
                {
                    name: "View Details",
                    onClick: () => {
                        notification.add(details, {
                            title: `Prescription #${data.prescription_id}`,
                            type: "info",
                        });
                    },
                },
                {
                    name: "Load",
                    primary: true,
                    onClick: async () => {
                        try {
                            const pos = this.env.services.pos;
                            const order = pos.get_order();

                            if (!order) {
                                notification.add("⚠️ No active order found", { type: "warning" });
                                return;
                            }

                            const partnerId = data.partner_id;
                            if (!partnerId) {
                                notification.add("❌ No patient linked", { type: "danger" });
                                return;
                            }

                            const partner = pos.db.get_partner_by_id(partnerId);
                            if (!partner) {
                                notification.add(
                                    `❌ Customer not found for ID ${partnerId}`,
                                    { type: "danger" }
                                );
                                return;
                            }

                            await order.set_partner(partner);
                            notification.add(
                                `✅ Prescription loaded for ${partner.name}`,
                                { type: "success" }
                            );

//                            this.removePendingNotification(data.prescription_id);
                        } catch (error) {
                            console.error("❌ Error in Load button:", error);
                            notification.add("❌ Failed to load prescription", { type: "danger" });
                        }
                    },
                },
            ],
        });

        this.playNotificationSound();
    },

    playNotificationSound() {
        try {
            const audio = new Audio(
                "/point_of_sale/static/src/sounds/notification.wav"
            );
            audio.volume = 1;
            audio.play().catch((err) =>
                console.warn("⚠️ Sound blocked:", err)
            );
        } catch (error) {
            console.warn("❌ Sound not available:", error);
        }
    },

    // --- Persistence handling (unchanged) ---

//    savePendingNotification(data) {
//        const stored =
//            JSON.parse(localStorage.getItem("pending_prescriptions") || "[]") ||
//            [];
//
//        const exists = stored.some(
//            (n) => n.prescription_id === data.prescription_id
//        );
//
//        if (!exists) {
//            stored.push(data);
//            localStorage.setItem(
//                "pending_prescriptions",
//                JSON.stringify(stored)
//            );
//            console.log("💾 Saved pending prescription:", data.prescription_id);
//        }
//    },

//    removePendingNotification(prescriptionId) {
//        let stored =
//            JSON.parse(localStorage.getItem("pending_prescriptions") || "[]") ||
//            [];
//
//        stored = stored.filter(
//            (n) => n.prescription_id !== prescriptionId
//        );
//
//        localStorage.setItem(
//            "pending_prescriptions",
//            JSON.stringify(stored)
//        );
//
//        console.log(
//            "🗑️ Removed prescription from pending list:",
//            prescriptionId
//        );
//    },

//    restorePendingNotifications() {
//        const stored =
//            JSON.parse(localStorage.getItem("pending_prescriptions") || "[]") ||
//            [];
//
//        if (stored.length) {
//            console.log("♻️ Restoring pending prescriptions:", stored);
//            for (const data of stored) {
//                this.handlePrescriptionNotification(data, this.notification);
//            }
//        }
//    },
};

registry
    .category("services")
    .add("prescription_notification", prescriptionNotificationService);
