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
};

registry
    .category("services")
    .add("prescription_notification", prescriptionNotificationService);
