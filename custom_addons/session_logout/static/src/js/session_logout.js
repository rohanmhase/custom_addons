/** @odoo-module **/

import { registry } from "@web/core/registry";
import { browser } from "@web/core/browser/browser";

const AUTO_LOGOUT_SERVICE = {
    dependencies: ["rpc"],

    start(env, { rpc }) {
        const TIMEOUT_DURATION = 8 * 60 * 60 * 1000; // 1 hour in milliseconds
        const WARNING_DURATION = 5 * 60 * 1000; // 5 minutes warning

        let timeoutId = null;
        let warningTimeoutId = null;
        let warningShown = false;

        function logout() {
            console.log("Auto logout triggered");
            browser.location.href = "/web/session/logout?redirect=/web/login";
        }

        function showWarning() {
            if (!warningShown) {
                warningShown = true;
                const stayLoggedIn = confirm(
                    "Your session will expire in 5 minutes due to inactivity. " +
                    "Click OK to stay logged in."
                );
                if (stayLoggedIn) {
                    resetTimer();
                }
            }
        }

        function resetTimer() {
            // Clear existing timers
            if (timeoutId) clearTimeout(timeoutId);
            if (warningTimeoutId) clearTimeout(warningTimeoutId);
            warningShown = false;

            // Set warning timer (5 minutes before logout)
            warningTimeoutId = setTimeout(showWarning, TIMEOUT_DURATION - WARNING_DURATION);

            // Set logout timer
            timeoutId = setTimeout(logout, TIMEOUT_DURATION);

            // Extend session on server
            rpc("/web/session/extend", {}).catch(() => {
                // If session extend fails, session might be invalid
                console.log("Session extend failed");
            });
        }

        // Events that indicate user activity
        const activityEvents = [
            "mousedown",
            "mousemove",
            "keypress",
            "scroll",
            "touchstart",
            "click",
        ];

        // Throttle reset timer to avoid too many calls
        let lastReset = Date.now();
        const THROTTLE_DELAY = 30000; // Only reset every 30 seconds max

        function throttledReset() {
            const now = Date.now();
            if (now - lastReset > THROTTLE_DELAY) {
                lastReset = now;
                resetTimer();
            } else {
                // Just clear timeouts without server call
                if (timeoutId) clearTimeout(timeoutId);
                if (warningTimeoutId) clearTimeout(warningTimeoutId);
                warningTimeoutId = setTimeout(showWarning, TIMEOUT_DURATION - WARNING_DURATION);
                timeoutId = setTimeout(logout, TIMEOUT_DURATION);
            }
        }

        // Add event listeners
        activityEvents.forEach(event => {
            document.addEventListener(event, throttledReset, true);
        });

        // Initialize timer
        resetTimer();

        // Handle browser/tab close (optional - logs out immediately)
        window.addEventListener("beforeunload", () => {
            // Use sendBeacon for async logout on browser close
            const logoutUrl = "/web/session/logout";
            if (navigator.sendBeacon) {
                navigator.sendBeacon(logoutUrl);
            }
        });

        console.log("Auto logout service started - timeout: 1 hour");
    },
};

registry.category("services").add("auto_logout", AUTO_LOGOUT_SERVICE);