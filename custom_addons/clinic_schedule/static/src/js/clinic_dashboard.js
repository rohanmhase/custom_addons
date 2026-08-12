/** @odoo-module **/
import {registry} from "@web/core/registry";
import {Component, useState, onWillStart} from "@odoo/owl";
import {useService} from "@web/core/utils/hooks";

const DateTime = window.luxon ? window.luxon.DateTime : luxon.DateTime;

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

        // DEFAULT DATE IS ALWAYS TOMORROW (NEXT DAY)
        const tomorrowISO = DateTime.now().setZone('Asia/Kolkata').plus({days: 1}).toISODate();
        this.baseTimeSlots = generatedSlots;

        this.state = useState({
            activeTab: "matrix",
            selectedRegion: 0,
            selectedClinic: 0,
            selectedDate: tomorrowISO,
            lastFetchedDate: tomorrowISO,
            lastFetchedClinic: 0,
            pulledTherapistIds: [],
            massReassignTarget: 0,
            timeSlots: generatedSlots,
            slotsLocked: false,
            therapists: [],
            appointments: [],
            clinics: [],
            regions: [],
            rosterData: [],
            kpis: {
                rs_count: 0, fixed_count: 0, floater_count: 0, hv_count: 0, utilization: 0,
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
            allotFilterDesignation: "all",
            allotFilterGender: "all",
            allotFilterRegion: "all", // NEW
            allotFilterClinic: "all", // NEW
            allotFilterStatus: "all", // NEW
            selectedTherapistObj: null,
            isTherapistActionModalOpen: false,
            selectedTherapistForAction: null,
            isLateModalOpen: false,
            lateExpectedHour: "10",
            isSmartViewOpen: false,
            smartViewData: null,
            smartViewClinicName: "",
            // TODAY'S PREVIEW MODAL STATE
            isTodayPreviewOpen: false,
            todayPreviewData: null,
            is_manager: false,
            isSubstituteModalOpen: false,
            substituteTargetPlaceholderId: null,
        });

        onWillStart(async () => {
            await this.loadUserDefaults();
            await this.refreshGrid();
            await this.loadRosterMetadata();
        });
    }

    setAllotFilter(type, value) {
        if (type === 'designation') this.state.allotFilterDesignation = value;
        if (type === 'gender') this.state.allotFilterGender = value;
        if (type === 'region') this.state.allotFilterRegion = value;
        if (type === 'clinic') this.state.allotFilterClinic = value;
        if (type === 'status') this.state.allotFilterStatus = value;
    }

    async removeTherapistFromBoard() {
        if (!this.state.selectedTherapistForAction) return;
        const confirmed = window.confirm(`Are you sure you want to remove ${this.state.selectedTherapistForAction.name} from this branch? All their patients today will be dumped to UNASSIGNED.`);
        if (!confirmed) return;
        try {
            const res = await this.orm.call("clinic.schedule.appointment", "action_remove_therapist_from_board", [
                this.state.selectedTherapistForAction.id,
                parseInt(this.state.selectedClinic),
                this.state.selectedDate
            ]);
            this.notificationService.add(res.message, { type: res.status });
        } catch (e) { console.error(e); }
        this.closeTherapistActionModal();
        await this.refreshGrid();
        await this.loadRosterMetadata();
    }

    get unassignedAppointments() {
        return this.state.appointments.filter(a => a.therapist_id === 0);
    }

    async carryForward() {
        if (!this.state.selectedClinic) return;
        const confirmed = window.confirm(`Are you sure you want to pull yesterday's completed patients into ${this.state.selectedDate}?`);
        if (!confirmed) return;
        try {
            const res = await this.orm.call("clinic.schedule.appointment", "action_carry_forward_schedule", [
                parseInt(this.state.selectedClinic),
                this.state.selectedDate
            ]);
            this.notificationService.add(res.message, { type: res.status === 'success' ? 'success' : 'info' });
            await this.refreshGrid();
        } catch (error) {
            console.error(error);
        }
    }

    onAppointmentClick(appointment) {
        this.state.selectedAppointment = appointment;
        const freeStaff = this.getFreeTherapistsForHour(appointment.slot_key, appointment.patient_raw_gender);
        this.state.quickReassignTherapist = freeStaff.length > 0 ? freeStaff[0].id : 0;
        this.state.isActionModalOpen = true;
    }

    getFreeTherapistsForHour(slotKey, patientGender = false) {
        if (!this.state.therapists || !this.state.appointments) return [];
        const timeToMins = (str) => {
            let [h, m] = str.split(':').map(Number);
            return h * 60 + m;
        };
        const targetMins = timeToMins(slotKey);
        return this.state.therapists.filter(t => {
            if (t.id === 0) return false;
            if (t.is_absent) return false;
            if (this.state.selectedAppointment && t.id === this.state.selectedAppointment.therapist_id) return false;
            if (patientGender && t.raw_gender && patientGender !== t.raw_gender) return false;
            const isOccupied = this.state.appointments.some(a => {
                if (a.therapist_id !== t.id) return false;
                if (a.attendance_state === 'no_show') return false;
                let startMins = timeToMins(a.slot_key);
                let durationMins = (a.col_span || 1) * 10;
                let endMins = startMins + durationMins;
                return targetMins >= startMins && targetMins < endMins;
            });
            return !isOccupied;
        });
    }

    async switchTab(tabName) {
        this.state.activeTab = tabName;
        if (tabName === "roster") {
            await this.loadRosterMetadata();
        } else if (tabName === "attendance") {
            await this.loadAttendanceLedger();
        } else {
            await this.refreshGrid();
        }
    }

    async onRegionChange() {
        const availableClinics = this.filteredClinics;
        const currentRegion = parseInt(this.state.selectedRegion) || 0;
        if (availableClinics.length > 0) {
            this.state.selectedClinic = availableClinics[0].id;
            await this.orm.call("clinic.schedule.appointment", "save_last_operated_clinic", [availableClinics[0].id, currentRegion]);
        } else {
            this.state.selectedClinic = 0;
            await this.orm.call("clinic.schedule.appointment", "save_last_operated_clinic", [0, currentRegion]);
        }
        await this.refreshGrid();
    }

    async onClinicChange() {
        const currentClinic = parseInt(this.state.selectedClinic) || 0;
        const currentRegion = parseInt(this.state.selectedRegion) || 0;
        if (currentClinic > 0 || currentRegion > 0) {
            await this.orm.call("clinic.schedule.appointment", "save_last_operated_clinic", [currentClinic, currentRegion]);
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
        this.state.timeSlots = [...this.baseTimeSlots];

        if (data.selected_clinic_id && !this.state.selectedClinic) {
            this.state.selectedClinic = data.selected_clinic_id;
            const currentRegion = parseInt(this.state.selectedRegion) || 0;
            await this.orm.call("clinic.schedule.appointment", "save_last_operated_clinic", [data.selected_clinic_id, currentRegion]);
        }
        if (this.state.activeTab === "roster") await this.loadRosterMetadata();
        if (this.state.activeTab === "attendance") await this.loadAttendanceLedger();
    }

    async toggleTodayTomorrow() {
        const todayISO = DateTime.now().setZone('Asia/Kolkata').toISODate();
        const tomorrowISO = DateTime.now().setZone('Asia/Kolkata').plus({days: 1}).toISODate();
        if (this.state.selectedDate === todayISO) {
            this.state.selectedDate = tomorrowISO;
        } else {
            this.state.selectedDate = todayISO;
        }
        await this.refreshGrid();
    }

    async loadRosterMetadata() {
        this.state.rosterData = await this.orm.call("clinic.schedule.appointment", "get_roster_data", [this.state.selectedDate]);
    }

    async loadAttendanceLedger() {
        this.state.attendanceLedger = await this.orm.call("clinic.schedule.appointment", "get_attendance_ledger", [this.state.selectedDate]) || [];
        this.state.expandedRows = [];
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
        const activeSlot = slots.find(e => e.attendance_state !== 'no_show');
        if (activeSlot) return activeSlot;
        return slots[0];
    }

    getTherapistRowCells(therapistId) {
        const cells = [];
        let skipUntilIndex = -1;
        this.state.timeSlots.forEach((slotKey, index) => {
            if (index < skipUntilIndex) return;
            const slots = this.state.appointments.filter(
                app => app.therapist_id === therapistId && app.slot_key === slotKey
            );
            if (slots.length > 0) {
                const maxSpan = Math.max(...slots.map(s => s.col_span || 6));
                cells.push({isApp: true, appointments: slots, colspan: maxSpan, slotKey: slotKey});
                skipUntilIndex = index + maxSpan;
            } else {
                cells.push({isApp: false, appointments: [], colspan: 1, slotKey: slotKey});
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
        const dt = DateTime.fromObject({
            year: parseInt(year, 10),
            month: parseInt(month, 10),
            day: parseInt(day, 10),
            hour: parseInt(hStr, 10),
            minute: parseInt(mStr, 10)
        }, {zone: 'Asia/Kolkata'});
        return dt.toUTC().toFormat("yyyy-MM-dd HH:mm:ss");
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
        this.state.allotableTherapists = data;
        this.state.allotSearchQuery = "";
        this.state.allotFilterDesignation = "all";
        this.state.allotFilterGender = "all";
        this.state.allotFilterRegion = "all"; // NEW
        this.state.allotFilterClinic = "all"; // NEW
        this.state.allotFilterStatus = "all"; // NEW
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
        const tObj = this.state.selectedTherapistObj;
        if (tObj.is_working_elsewhere) {
            const msg = `Transit Warning: ${tObj.name} is already booked at ${tObj.working_clinics} today. Are you sure you want to allot them here?`;
            if (!window.confirm(msg)) {
                return;
            }
        }
        const tId = tObj.id;
        await this.orm.write("clinic.therapist", [tId], {allowed_branch_ids: [[4, parseInt(this.state.selectedClinic)]]});
        if (!this.state.pulledTherapistIds.includes(tId)) this.state.pulledTherapistIds.push(tId);
        this.closeAllotModal();
        await this.refreshGrid();
        await this.loadRosterMetadata();
    }

    async requestFloater() {
        const action = await this.orm.call(
            "clinic.schedule.appointment",
            "action_check_floater_eligibility",
            [parseInt(this.state.selectedClinic), this.state.selectedDate]
        );
        if (action) {
            this.actionService.doAction(action, {
                onClose: async () => {
                    await this.refreshGrid();
                    await this.loadRosterMetadata();
                }
            });
        }
    }

    async rejectFloater(placeholderId) {
        await this.orm.call("clinic.schedule.appointment", "action_reject_floater", [placeholderId]);
        this.notificationService.add("Floater request rejected and patients unassigned.", { type: "success" });
        await this.refreshGrid();
    }

    async openSubstituteModal(placeholderId) {
        this.state.substituteTargetPlaceholderId = placeholderId;
        const displayedIds = this.state.therapists.map(t => t.id);
        const data = await this.orm.call("clinic.schedule.appointment", "get_allotable_therapists", [parseInt(this.state.selectedClinic), this.state.selectedDate, displayedIds]);
        this.state.allotableTherapists = data.map(t => {
            let typeTag = t.designation === 'fixed' ? "[FIXED]" : (t.designation === 'floater' ? "[FLOAT]" : "[HV]");
            let genderTag = t.gender === 'm' ? "(M)" : (t.gender === 'f' ? "(F)" : "");
            return {...t, smart_name: `${typeTag} ${t.name} ${genderTag}`.trim()};
        });
        this.state.allotSearchQuery = "";
        this.state.selectedTherapistObj = null;
        this.state.isSubstituteModalOpen = true;
    }

    closeSubstituteModal() {
        this.state.isSubstituteModalOpen = false;
        this.state.substituteTargetPlaceholderId = null;
        this.state.selectedTherapistObj = null;
    }

    async confirmSubstitute() {
        if (!this.state.selectedTherapistObj || !this.state.substituteTargetPlaceholderId) return;
        const realTId = this.state.selectedTherapistObj.id;
        await this.orm.call("clinic.schedule.appointment", "action_substitute_floater", [this.state.substituteTargetPlaceholderId, realTId]);
        this.notificationService.add("Floater substituted successfully. Patients have been moved.", { type: "success" });
        this.closeSubstituteModal();
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
        try {
            const response = await this.orm.call("clinic.schedule.appointment", "apply_therapist_action",
                [this.state.selectedTherapistForAction.id, parseInt(this.state.selectedClinic), this.state.selectedDate, actionName, 10]
            );
            if (response && response.message) {
                this.notificationService.add(response.message, {type: response.status === 'success' ? 'success' : 'warning'});
            }
        } catch (error) {
            console.error(error);
        }
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
        try {
            const response = await this.orm.call("clinic.schedule.appointment", "apply_therapist_action",
                [this.state.selectedTherapistForAction.id, parseInt(this.state.selectedClinic), this.state.selectedDate, 'late', parseInt(this.state.lateExpectedHour)]
            );
            if (response && response.message) {
                this.notificationService.add(response.message, {type: response.status === 'success' ? 'success' : 'warning'});
            }
        } catch (error) {
            console.error(error);
        }
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
        if (actionName === 'action_send_test_notification') {
            this.notificationService.add("WhatsApp API credentials pending. Sandbox dispatch disabled.", {type: "warning"});
            return;
        }
        await this.orm.call("clinic.schedule.appointment", actionName, [[this.state.selectedAppointment.id]]);
        this.closeActionModal();
        await this.refreshGrid();
    }

    async quickRemoveSlot(ev, appId, currentTherapistId) {
        ev.stopPropagation();
        if (!appId) return;
        try {
            await this.orm.unlink("clinic.schedule.appointment", [appId]);
            this.notificationService.add("Slot successfully deleted from the board.", {type: "success"});
            await this.refreshGrid();
        } catch (error) {
            console.error(error);
        }
    }

    async unassignSlot() {
        if (!this.state.selectedAppointment) return;
        await this.orm.unlink("clinic.schedule.appointment", [this.state.selectedAppointment.id]);
        this.closeActionModal();
        this.notificationService.add("Slot successfully deleted from the board.", {type: "success"});
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
        const therapist = this.state.therapists.find(t => t.id === therapistId);
        if (therapist && therapist.is_absent) {
            this.notificationService.add(`Cannot book. ${therapist.name} is currently marked as ${therapist.overlay_state.toUpperCase()}.`, {type: "danger"});
            return;
        }
        const existing = this.getSlotData(therapistId, slotKey);
        if (existing && existing.is_other_clinic) {
            this.notificationService.add(`Cannot modify. This therapist is scheduled at ${existing.other_clinic_name} during this time.`, {type: "warning"});
            return;
        }
        if (existing && existing.attendance_state !== 'no_show') {
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
        this.notificationService.add("WhatsApp API credentials pending. Mass dispatch disabled.", {type: "warning"});
        this.state.slotsLocked = false;
    }

    get filteredAttendance() {
        const query = this.state.attendanceSearchQuery.toLowerCase().trim();
        if (!query) return this.state.attendanceLedger;
        return this.state.attendanceLedger.filter(t => t.name.toLowerCase().includes(query));
    }

    get filteredAllotableTherapists() {
        let list = this.state.allotableTherapists;

        if (this.state.allotFilterDesignation !== 'all') {
            list = list.filter(t => t.designation === this.state.allotFilterDesignation);
        }
        if (this.state.allotFilterGender !== 'all') {
            list = list.filter(t => t.gender === this.state.allotFilterGender);
        }
        if (this.state.allotFilterStatus !== 'all') {
            list = list.filter(t => t.status === this.state.allotFilterStatus);
        }
        if (this.state.allotFilterRegion !== 'all') {
            const regId = parseInt(this.state.allotFilterRegion);
            list = list.filter(t => t.allowed_region_ids.includes(regId));
        }
        if (this.state.allotFilterClinic !== 'all') {
            const clinId = parseInt(this.state.allotFilterClinic);
            list = list.filter(t => t.allowed_clinic_ids.includes(clinId));
        }

        const query = this.state.allotSearchQuery.toLowerCase().trim();
        if (query) {
            list = list.filter(t =>
                (t.name && t.name.toLowerCase().includes(query)) ||
                (t.vendor_id && t.vendor_id.toLowerCase().includes(query)) ||
                (t.allowed_clinics_names && t.allowed_clinics_names.toLowerCase().includes(query))
            );
        }
        return list;
    }

    get filteredClinics() {
        const regionId = parseInt(this.state.selectedRegion);
        if (!regionId) return this.state.clinics;
        return this.state.clinics.filter(c => c.region_id && c.region_id[0] === regionId);
    }

    get isToday() {
        const todayISO = DateTime.now().setZone('Asia/Kolkata').toISODate();
        return this.state.selectedDate === todayISO;
    }

    get isTomorrow() {
        const tomorrowISO = DateTime.now().setZone('Asia/Kolkata').plus({days: 1}).toISODate();
        return this.state.selectedDate === tomorrowISO;
    }

    async triggerMassReassign() {
        if (!this.state.selectedTherapistForAction || !this.state.massReassignTarget) return;
        const res = await this.orm.call("clinic.schedule.appointment", "action_mass_reassign_sessions", [
            this.state.selectedTherapistForAction.id,
            parseInt(this.state.massReassignTarget),
            parseInt(this.state.selectedClinic),
            this.state.selectedDate
        ]);
        this.notificationService.add(res.message, { type: res.status });
        this.closeTherapistActionModal();
        await this.refreshGrid();
    }

    async loadUserDefaults() {
        try {
            const defaults = await this.orm.call("clinic.schedule.appointment", "get_user_schedule_defaults", []);
            if (defaults) {
                if (defaults.default_region_id) {
                    this.state.selectedRegion = defaults.default_region_id;
                }
                if (defaults.last_operated_clinic_id) {
                    this.state.selectedClinic = defaults.last_operated_clinic_id;
                }
                this.state.is_manager = defaults.is_manager || false;
            }
        } catch (e) {
            console.error("Error loading user schedule defaults:", e);
        }
    }
}

ClinicMatrixDashboard.template = "clinic_schedule.ClinicMatrixDashboardTemplate";
registry.category("actions").add("clinic_schedule.matrix_dashboard_action", ClinicMatrixDashboard);