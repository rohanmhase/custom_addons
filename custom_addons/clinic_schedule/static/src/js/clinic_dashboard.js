/** @odoo-module **/

import {registry} from "@web/core/registry";
import {Component, useState, onWillStart} from "@odoo/owl";
import {useService} from "@web/core/utils/hooks";

export class ClinicMatrixDashboard extends Component {
    setup() {
        this.orm = useService("orm");
        this.actionService = useService("action");
        this.notificationService = useService("notification");

        // GENERATE 10-MINUTE SLOTS (07:00 to 21:50)
        let generatedSlots = [];
        for (let h = 7; h <= 21; h++) {
            for (let m = 0; m < 60; m += 10) {
                generatedSlots.push(`${h.toString().padStart(2, '0')}:${m.toString().padStart(2, '0')}`);
            }
        }

        this.state = useState({
            activeTab: "matrix",
            selectedRegion: 0,
            selectedClinic: 0,
            selectedDate: new Date().toISOString().split('T')[0],
            lastFetchedDate: new Date().toISOString().split('T')[0],
            lastFetchedClinic: 0,
            pulledTherapistIds: [],
            timeSlots: generatedSlots,

            slotsLocked: false,

            therapists: [],
            appointments: [],
            clinics: [],
            regions: [],
            rosterData: [],

            kpis: {
                rs_count: 0, fixed_count: 0, floater_count: 0, utilization: 0,
                total_scheduled: 0, allotted_clinic_hv: 0, self_scheduled: 0, outstanding: 0
            },

            attendanceLedger: [],
            attendanceSearchQuery: "",
            expandedRows: [],
            selectedAppointment: null,
            isActionModalOpen: false,
            quickReassignTherapist: 0,
            isAllotModalOpen: false,
            allotableTherapists: [],
            allotSearchQuery: "",
            selectedTherapistObj: null,
            analyticsData: null,
            isDrillDownModalOpen: false,
            drillDownTitle: "",
            drillDownRecords: [],
            drillDownType: "",
            isTherapistActionModalOpen: false,
            selectedTherapistForAction: null,
            isLateModalOpen: false,
            lateExpectedHour: "10",
            isSmartViewOpen: false,
            smartViewData: null,
            smartViewClinicName: ""
        });

        onWillStart(async () => {
            await this.refreshGrid();
            await this.loadRosterMetadata();
        });
    }

    get filteredAttendance() {
        const query = this.state.attendanceSearchQuery.toLowerCase().trim();
        if (!query) return this.state.attendanceLedger;
        return this.state.attendanceLedger.filter(t => t.name.toLowerCase().includes(query));
    }

    get filteredAllotableTherapists() {
        const query = this.state.allotSearchQuery.toLowerCase().trim();
        if (!query) return this.state.allotableTherapists;
        return this.state.allotableTherapists.filter(t =>
            (t.smart_name && t.smart_name.toLowerCase().includes(query)) ||
            (t.vendor_id && t.vendor_id.toLowerCase().includes(query)) ||
            (t.badge_id && t.badge_id.toLowerCase().includes(query))
        );
    }

    get filteredClinics() {
        const regionId = parseInt(this.state.selectedRegion);
        if (!regionId) return this.state.clinics;
        return this.state.clinics.filter(c => c.region_id && c.region_id[0] === regionId);
    }

    getFreeTherapistsForHour(slotKey, patientGender = false) {
        if (!this.state.therapists || !this.state.appointments) return [];
        return this.state.therapists.filter(t => {
            if (t.id === 0) return false;
            if (t.is_absent) return false;
            if (this.state.selectedAppointment && t.id === this.state.selectedAppointment.therapist_id) return false;

            if (patientGender && t.raw_gender) {
                if (patientGender === 'm' && t.raw_gender === 'f') return false;
                if (patientGender === 'f' && t.raw_gender === 'm') return false;
            }

            const hasSlot = this.state.appointments.some(a => a.therapist_id === t.id && a.slot_key === slotKey);
            return !hasSlot;
        });
    }

    async switchTab(tabName) {
        this.state.activeTab = tabName;
        if (tabName === "roster") {
            await this.loadRosterMetadata();
        } else if (tabName === "attendance") {
            await this.loadAttendanceLedger();
        } else if (tabName === "analytics") {
            await this.loadAnalyticsData();
        } else {
            await this.refreshGrid();
        }
    }

    async onRegionChange() {
        const availableClinics = this.filteredClinics;
        if (availableClinics.length > 0) {
            this.state.selectedClinic = availableClinics[0].id;
        } else {
            this.state.selectedClinic = 0;
        }
        await this.refreshGrid();
    }

    async refreshGrid() {
        const currentClinic = parseInt(this.state.selectedClinic) || 0;
        if (this.state.lastFetchedDate !== this.state.selectedDate || parseInt(this.state.lastFetchedClinic) !== currentClinic) {
            this.state.pulledTherapistIds = [];
            this.state.lastFetchedDate = this.state.selectedDate;
            this.state.lastFetchedClinic = currentClinic;
        }

        const data = await this.orm.call("clinic.schedule.appointment", "get_matrix_data", [currentClinic, this.state.selectedDate, this.state.pulledTherapistIds]);

        this.state.clinics = data.clinics || [];
        this.state.regions = data.regions || [];
        this.state.therapists = data.therapists || [];
        this.state.appointments = data.appointments || [];
        this.state.kpis = data.kpis || this.state.kpis;

        if (data.selected_clinic_id && !this.state.selectedClinic) {
            this.state.selectedClinic = data.selected_clinic_id;
        }

        if (this.state.activeTab === "analytics") await this.loadAnalyticsData();
        if (this.state.activeTab === "roster") await this.loadRosterMetadata();
        if (this.state.activeTab === "attendance") await this.loadAttendanceLedger();
    }

    async loadRosterMetadata() {
        this.state.rosterData = await this.orm.call("clinic.schedule.appointment", "get_roster_data", [this.state.selectedDate]);
    }

    async loadAttendanceLedger() {
        this.state.attendanceLedger = await this.orm.call("clinic.schedule.appointment", "get_attendance_ledger", [this.state.selectedDate]) || [];
        this.state.expandedRows = [];
    }

    async loadAnalyticsData() {
        this.state.analyticsData = await this.orm.call("clinic.schedule.appointment", "get_daily_analytics", [this.state.selectedDate]) || null;
    }

    async openSmartView() {
        const clinicId = parseInt(this.state.selectedClinic);
        if (!clinicId) return;
        const clinicObj = this.state.clinics.find(c => c.id === clinicId);
        this.state.smartViewClinicName = clinicObj ? clinicObj.name : "Selected Branch";
        this.state.smartViewData = await this.orm.call("clinic.schedule.appointment", "get_clinic_smart_view", [clinicId, this.state.selectedDate]);
        this.state.isSmartViewOpen = true;
    }

    closeSmartView() {
        this.state.isSmartViewOpen = false;
        this.state.smartViewData = null;
    }

    openDrillDown(metricKey, title, type) {
        if (!this.state.analyticsData || !this.state.analyticsData.drill_downs) return;
        this.state.drillDownRecords = this.state.analyticsData.drill_downs[metricKey] || [];
        this.state.drillDownTitle = title;
        this.state.drillDownType = type;
        this.state.isDrillDownModalOpen = true;
    }

    closeDrillDown() {
        this.state.isDrillDownModalOpen = false;
        this.state.drillDownRecords = [];
    }

    toggleRow(therapistId) {
        if (this.state.expandedRows.includes(therapistId)) {
            this.state.expandedRows = this.state.expandedRows.filter(id => id !== therapistId);
        } else {
            this.state.expandedRows.push(therapistId);
        }
    }

    getSlotData(therapistId, slotKey) {
        const slots = this.state.appointments.filter(app => app.therapist_id === therapistId && app.slot_key === slotKey);
        if (slots.length === 0) return null;
        return slots.find(e => e.slot_type === 'patient') || slots[0];
    }

    getTherapistRowCells(therapistId) {
        let cells = [];
        let skipUntilIndex = -1;

        this.state.timeSlots.forEach((slotKey, index) => {
            if (index < skipUntilIndex) return;

            const slots = this.state.appointments.filter(app => app.therapist_id === therapistId && app.slot_key === slotKey);
            const appointment = slots.length > 0 ? (slots.find(e => e.slot_type === 'patient') || slots[0]) : null;

            if (appointment) {
                let span = appointment.col_span || 6;
                cells.push({isApp: true, appointment: appointment, colspan: span, slotKey: slotKey});
                skipUntilIndex = index + span;
            } else {
                cells.push({isApp: false, appointment: null, colspan: 1, slotKey: slotKey});
            }
        });

        return cells;
    }

    formatHourLabel(slotKey) {
        if (!slotKey) return "";
        let [hStr, mStr] = slotKey.split(':');
        let hour = parseInt(hStr, 10);
        let period = hour >= 12 ? 'PM' : 'AM';
        let displayHour = hour % 12 || 12;
        return `${displayHour.toString().padStart(2, '0')}:${mStr} ${period}`;
    }

    getUtcDateTimeString(slotKey) {
        let [year, month, day] = this.state.selectedDate.split('-');
        let [hStr, mStr] = slotKey.split(':');
        let localDate = new Date(year, month - 1, day, parseInt(hStr, 10), parseInt(mStr, 10), 0);
        let utcYear = localDate.getUTCFullYear();
        let utcMonth = (localDate.getUTCMonth() + 1).toString().padStart(2, '0');
        let utcDay = localDate.getUTCDate().toString().padStart(2, '0');
        let utcHour = localDate.getUTCHours().toString().padStart(2, '0');
        let utcMin = localDate.getUTCMinutes().toString().padStart(2, '0');
        return `${utcYear}-${utcMonth}-${utcDay} ${utcHour}:${utcMin}:00`;
    }

    async onCreateNewTherapistClick() {
        this.actionService.doAction({
            type: "ir.actions.act_window",
            res_model: "clinic.therapist",
            views: [[false, "form"]],
            target: "new",
            context: {default_allowed_branch_ids: this.state.selectedClinic ? [parseInt(this.state.selectedClinic)] : []}
        }, {
            onClose: async () => {
                await this.refreshGrid();
                await this.loadRosterMetadata();
            }
        });
    }

    async openAllotModal() {
        const displayedIds = this.state.therapists.map(t => t.id);
        const data = await this.orm.call("clinic.schedule.appointment", "get_allotable_therapists", [parseInt(this.state.selectedClinic), this.state.selectedDate, displayedIds]);

        this.state.allotableTherapists = data.map(t => {
            let typeTag = t.designation === 'fixed' || t.designation === 'rs' ? "[FIXED]" : (t.designation === 'floater' ? "[FLOAT]" : "[HV]");
            let genderTag = t.gender === 'm' ? "(M)" : (t.gender === 'f' ? "(F)" : "");
            return {...t, smart_name: `${typeTag} ${t.name} ${genderTag}`.trim()};
        });

        this.state.allotSearchQuery = "";
        this.state.selectedTherapistObj = null;
        this.state.isAllotModalOpen = true;
    }

    selectTherapistToAllot(therapist) {
        this.state.selectedTherapistObj = therapist;
    }

    closeAllotModal() {
        this.state.isAllotModalOpen = false;
        this.state.selectedTherapistObj = null;
    }

    async confirmAllotTherapist() {
        if (!this.state.selectedTherapistObj) return;
        const tId = this.state.selectedTherapistObj.id;
        await this.orm.write("clinic.therapist", [tId], {allowed_branch_ids: [[4, parseInt(this.state.selectedClinic)]]});
        if (!this.state.pulledTherapistIds.includes(tId)) this.state.pulledTherapistIds.push(tId);
        this.closeAllotModal();
        await this.refreshGrid();
        await this.loadRosterMetadata();
    }

    openTherapistActionModal(therapistId, therapistName) {
        if (therapistId === 0) return;
        this.state.selectedTherapistForAction = {id: therapistId, name: therapistName};
        this.state.isTherapistActionModalOpen = true;
    }

    closeTherapistActionModal() {
        this.state.isTherapistActionModalOpen = false;
        this.state.selectedTherapistForAction = null;
    }

    async toggleBufferState(therapistId) {
        if (!therapistId) return;
        await this.orm.call("clinic.therapist", "action_toggle_buffer", [[therapistId]]);
        this.closeTherapistActionModal();
        await this.refreshGrid();
        await this.loadRosterMetadata();
    }

    async applyTherapistAction(actionName) {
        if (!this.state.selectedTherapistForAction) return;

        if (actionName === 'late') {
            this.state.isLateModalOpen = true;
            this.state.isTherapistActionModalOpen = false;
            return;
        }

        await this.orm.call("clinic.schedule.appointment", "apply_therapist_action",
            [this.state.selectedTherapistForAction.id, parseInt(this.state.selectedClinic), this.state.selectedDate, actionName, 10]
        );

        this.closeTherapistActionModal();
        await this.refreshGrid();
        await this.loadRosterMetadata();
    }

    closeLateModal() {
        this.state.isLateModalOpen = false;
        this.state.lateExpectedHour = "10";
        this.state.selectedTherapistForAction = null;
    }

    async confirmLateAction() {
        if (!this.state.selectedTherapistForAction) return;
        await this.orm.call("clinic.schedule.appointment", "apply_therapist_action",
            [this.state.selectedTherapistForAction.id, parseInt(this.state.selectedClinic), this.state.selectedDate, 'late', parseInt(this.state.lateExpectedHour)]
        );
        this.closeLateModal();
        await this.refreshGrid();
        await this.loadRosterMetadata();
    }

    closeActionModal() {
        this.state.isActionModalOpen = false;
        this.state.selectedAppointment = null;
    }

    async triggerQuickAction(actionName) {
        if (!this.state.selectedAppointment) return;
        await this.orm.call("clinic.schedule.appointment", actionName, [[this.state.selectedAppointment.id]]);
        this.closeActionModal();
        await this.refreshGrid();
    }

    async unassignSlot() {
        if (!this.state.selectedAppointment) return;
        await this.orm.write("clinic.schedule.appointment", [this.state.selectedAppointment.id], {therapist_id: false});
        this.closeActionModal();
        await this.refreshGrid();
    }

    async reassignSlot(newTherapistIdRaw) {
        if (!this.state.selectedAppointment) return;
        const newTherapistId = parseInt(newTherapistIdRaw, 10);
        if (isNaN(newTherapistId)) return;
        await this.orm.write("clinic.schedule.appointment", [this.state.selectedAppointment.id], {therapist_id: newTherapistId === 0 ? false : newTherapistId});
        this.closeActionModal();
        await this.refreshGrid();
    }

    openFullForm() {
        if (!this.state.selectedAppointment) return;
        const appId = this.state.selectedAppointment.id;
        this.closeActionModal();
        this.actionService.doAction({
            type: "ir.actions.act_window",
            res_model: "clinic.schedule.appointment",
            res_id: appId,
            views: [[false, "form"]],
            target: "new"
        }, {onClose: () => this.refreshGrid()});
    }

    async onAddTimeClick(slotKey) {
        let startDt = this.getUtcDateTimeString(slotKey);
        this.actionService.doAction({
            type: "ir.actions.act_window",
            res_model: "clinic.schedule.appointment",
            views: [[false, "form"]],
            target: "new",
            context: {default_clinic_id: parseInt(this.state.selectedClinic), default_start_datetime: startDt}
        }, {onClose: () => this.refreshGrid()});
    }

    async onSlotClick(therapistId, slotKey) {
        const existing = this.getSlotData(therapistId, slotKey);
        if (existing) {
            this.state.selectedAppointment = existing;
            const freeStaff = this.getFreeTherapistsForHour(slotKey, existing.patient_raw_gender);
            if (freeStaff.length > 0) {
                this.state.quickReassignTherapist = freeStaff[0].id;
            } else {
                this.state.quickReassignTherapist = 0;
            }
            this.state.isActionModalOpen = true;
        } else {
            let startDt = this.getUtcDateTimeString(slotKey);
            this.actionService.doAction({
                type: "ir.actions.act_window",
                res_model: "clinic.schedule.appointment",
                views: [[false, "form"]],
                target: "new",
                context: {
                    default_clinic_id: parseInt(this.state.selectedClinic),
                    default_therapist_id: therapistId === 0 ? false : therapistId,
                    default_start_datetime: startDt
                }
            }, {onClose: () => this.refreshGrid()});
        }
    }

    toggleLockSlots() {
        this.state.slotsLocked = !this.state.slotsLocked;
        if (this.state.slotsLocked) {
            this.notificationService.add("Matrix is now locked. You can now dispatch mass notifications.", {type: "info"});
        }
    }

    async triggerMassSend() {
        const clinicId = parseInt(this.state.selectedClinic);
        if (!clinicId) return;

        const sentCount = await this.orm.call(
            "clinic.schedule.appointment",
            "action_mass_send_notifications",
            [clinicId, this.state.selectedDate]
        );

        this.notificationService.add(
            `Successfully dispatched ${sentCount} notifications.`,
            {type: "success", title: "Mass Dispatch Complete"}
        );

        this.state.slotsLocked = false;
    }
}

ClinicMatrixDashboard.template = "clinic_schedule.ClinicMatrixDashboardTemplate";
registry.category("actions").add("clinic_schedule.matrix_dashboard_action", ClinicMatrixDashboard);