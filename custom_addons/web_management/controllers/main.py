import uuid

from odoo import http
from odoo.http import request
from collections import defaultdict
from datetime import datetime


class PatientController(http.Controller):

    @http.route('/patient/<string:uuid>', type='http', auth='user', website=True)
    def patient_detail(self, uuid, **kwargs):
        patient = request.env['clinic.patient'].sudo().search([("uuid", "=", uuid)], limit=1)
        if not patient:
            return request.not_found()

        prescriptions = request.env["patient.prescription"].sudo().search(
            [("patient_id", "=", patient.id)],
            order="prescription_date desc"
        )

        case_taking = request.env["patient.case_taking"].sudo().search(
            [("patient_id", "=", patient.id)],
            order="case_taking_date desc"
        )

        session = request.env["patient.session"].sudo().search(
            [("patient_id", "=", patient.id)],
            order="session_date desc"
        )

        enrollment = request.env["patient.enrollment"].sudo().search(
            [("patient_id", "=", patient.id)],
            order="enrollment_date desc"
        )

        daily_followup = request.env["patient.daily_followup"].sudo().search(
            [("patient_id", "=", patient.id)],
            order="followup_date desc"
        )

        diet_chart = request.env["patient.diet_chart"].sudo().search(
            [("patient_id", "=", patient.id)],
            order="diet_taken_date desc"
        )

        followup = request.env["patient.followup"].sudo().search(
            [("patient_id", "=", patient.id)],
            order="weekly_followup_date desc"
        )

        attachment = request.env["patient.attachment"].sudo().search(
            [("patient_id", "=", patient.id)],
            order="attachment_date desc"
        )

        xray = request.env["patient.xray"].sudo().search(
            [("patient_id", "=", patient.id)],
            order="date_taken desc"
        )

        blood_report = request.env["patient.blood_report"].sudo().search(
            [("patient_id", "=", patient.id)],
            order="blood_report_date desc"
        )

        grouped_data = defaultdict(lambda: {"prescriptions": [],
                                            "case_taking": [],
                                            "session": [],
                                            "enrollment": [],
                                            "daily_followup": [],
                                            "diet_chart": [],
                                            "followup": [],
                                            "attachment": [],
                                            "xray": [],
                                            "blood_report": [],
                                            })

        # Group prescriptions by date
        for pres in prescriptions:
            if not pres.prescription_date:
                continue

            if isinstance(pres.prescription_date, datetime):
                date_key = pres.prescription_date.date()

            else:
                date_key = pres.prescription_date

            meds = [{
                "medicine": line.product_id.display_name,
                "qty": line.qty,
                "instructions": line.instructions or ""
            } for line in pres.line_ids]

            grouped_data[date_key]["prescriptions"].append({
                "doctor": pres.doctor_id.name,
                "modified_by": pres.write_uid.name,
                "state": pres.state,
                "notes": pres.notes,
                "meds": meds
            })

        # Group Case Taking by date
        for ct in case_taking:
            if not ct.case_taking_date:
                continue

            if isinstance(ct.case_taking_date, datetime):
                date_key = ct.case_taking_date.date()

            else:
                date_key = ct.case_taking_date

            grouped_data[date_key]["case_taking"].append({
                "doctor": ct.doctor_id.name,
                "modified_by": ct.write_uid.name,
                "k_c_o": ct.k_c_o,
                "p_h_o": ct.p_h_o,
                "s_h": ct.s_h,
                "f_h": ct.f_h,
                "allergies": ct.allergies,
                "habits": ct.habits,
                "mal": ct.mal,
                "mutra": ct.mutra,
                "nakta": ct.nakta,
                "kshudha": ct.kshudha,
                "nidra": ct.nidra,
                "jivha": ct.jivha,
                "crepts_rt": ct.crepts_rt,
                "crepts_lt": ct.crepts_lt,
                "shin_tenderness_rt": ct.shin_tenderness_rt,
                "shin_tenderness_lt": ct.shin_tenderness_lt,
                "swelling_rt": ct.swelling_rt,
                "swelling_lt": ct.swelling_lt,
                "rom_rt": ct.rom_rt,
                "rom_lt": ct.rom_lt,
                "slr_rt": ct.slr_rt,
                "slr_lt": ct.slr_lt,
                "pedal_oedema_rt": ct.pedal_oedema_rt,
                "pedal_oedema_lt": ct.pedal_oedema_lt,
                "pain_rt": ct.pain_rt,
                "pain_lt": ct.pain_lt,
                "deformity_rt": ct.deformity_rt,
                "deformity_lt": ct.deformity_lt,
                "diet": ct.diet,
                "diagnosis": ct.diagnosis,
                "adv_investigation": ct.adv_investigation,
                "adv_treatment": ct.adv_treatment,
                "adv_rx": ct.adv_rx,
                "treatment": ct.treatment,
                "notes": ct.notes,
            })

        # Group Therapy Session by date
        for sess in session:
            if not sess.session_date:
                continue

            if isinstance(sess.session_date, datetime):
                date_key = sess.session_date.date()

            else:
                date_key = sess.session_date

            grouped_data[date_key]["session"].append({
                "doctor": sess.doctor_id.name,
                "modified_by": sess.write_uid.name,
                "session_day": sess.session_day,
                "jivha": sess.jivha,
                "swelling": sess.swelling,
                "digestion": sess.digestion,
                "motion": sess.motion,
                "detox_therapy": sess.detox_therapy,
                "regeneration_therapy": sess.regeneration_therapy,
                "left_knee": sess.left_knee,
                "right_knee": sess.right_knee,
                "before_and_after_therapy_comment": sess.before_and_after_therapy_comment,
                "therapist_name": sess.therapist_name,
                "state": sess.state,
            })

        # Group Enrollment by date

        for en in enrollment:
            if not en.enrollment_date:
                continue

            if isinstance(en.enrollment_date, datetime):
                date_key = en.enrollment_date.date()

            else:
                date_key = en.enrollment_date

            grouped_data[date_key]["enrollment"].append({
                "doctor": en.doctor_id.name,
                "modified_by": en.write_uid.name,
                "enrollment_date": en.enrollment_date,
                "daily_sheet_ref": en.daily_sheet_ref,
                "total_amount": en.total_amount,
                "therapy_amount": en.therapy_amount,
                "first_cons_charges": en.first_cons_charges,
                "therapy_medicine": en.therapy_medicine,
                "total_sessions": en.total_sessions,
                "remaining_sessions": en.remaining_sessions,
                "used_sessions": en.used_sessions,
                "notes": en.notes,
                "state": en.state,
            })

        # Group Daily Followup by date

        for df in daily_followup:
            if not df.followup_date:
                continue

            if isinstance(df.followup_date, datetime):
                date_key = df.followup_date.date()

            else:
                date_key = df.followup_date

            grouped_data[date_key]["daily_followup"].append({
                "doctor": df.doctor_id.name,
                "modified_by": df.write_uid.name,
                "c_o": df.c_o,
                "mal": df.mal,
                "mutra": df.mutra,
                "nakta": df.nakta,
                "kshudha": df.kshudha,
                "nidra": df.nidra,
                "jivha": df.jivha,
                "crepts_rt": df.crepts_rt,
                "crepts_lt": df.crepts_lt,
                "shin_tenderness_rt": df.shin_tenderness_rt,
                "shin_tenderness_lt": df.shin_tenderness_lt,
                "swelling_rt": df.swelling_rt,
                "swelling_lt": df.swelling_lt,
                "rom_rt": df.rom_rt,
                "rom_lt": df.rom_lt,
                "slr_rt": df.slr_rt,
                "slr_lt": df.slr_lt,
                "pedal_oedema_rt": df.pedal_oedema_rt,
                "pedal_oedema_lt": df.pedal_oedema_lt,
                "pain_rt": df.pain_rt,
                "pain_lt": df.pain_lt,
                "deformity_rt": df.deformity_rt,
                "deformity_lt": df.deformity_lt,
                "notes": df.notes,
            })

        # Group Diet Chart by date

        for dc in diet_chart:
            if not dc.diet_taken_date:
                continue

            if isinstance(dc.diet_taken_date, datetime):
                date_key = dc.diet_taken_date.date()

            else:
                date_key = dc.diet_taken_date

            grouped_data[date_key]["diet_chart"].append({
                "doctor": dc.doctor_id.name,
                "modified_by": dc.write_uid.name,
                "diet_taken_date": dc.diet_taken_date,
                "therapy_day": dc.therapy_day,
                "morning_with_time": dc.morning_with_time,
                "lunch_with_time": dc.lunch_with_time,
                "evening_with_time": dc.evening_with_time,
                "dinner_with_time": dc.dinner_with_time,
                "comments": dc.comments,
            })

        # Group Followup by date

        for fl in followup:
            if not fl.weekly_followup_date:
                continue

            if isinstance(fl.weekly_followup_date, datetime):
                date_key = fl.weekly_followup_date.date()

            else:
                date_key = fl.weekly_followup_date

            grouped_data[date_key]["followup"].append({
                "doctor": fl.doctor_id.name,
                "modified_by": fl.write_uid.name,
                "weekly_followup_date": fl.weekly_followup_date,
                "weight": fl.weight,
                "diagnosis": fl.diagnosis,
                "k_c_o": fl.k_c_o,
                "investigation_status": fl.investigation_status,
                "morning_stiffness_with_duration_lt": fl.morning_stiffness_with_duration_lt,
                "b_l_shin_tenderness_with_gradation_lt": fl.b_l_shin_tenderness_with_gradation_lt,
                "b_l_knee_tenderness_with_gradation_and_position_lt": fl.b_l_knee_tenderness_with_gradation_and_position_lt,
                "b_l_shin_oedema_with_gradation_lt": fl.b_l_shin_oedema_with_gradation_lt,
                "pitting_oedema_lt": fl.pitting_oedema_lt,
                "non_pitting_oedema_lt": fl.non_pitting_oedema_lt,
                "b_l_shin_discoloration_rashes_itching_bruises_lt": fl.b_l_shin_discoloration_rashes_itching_bruises_lt,
                "local_knee_temperature_lt": fl.local_knee_temperature_lt,
                "heavy_free_lt": fl.heavy_free_lt,
                "complete_restricted_with_degree_lt": fl.complete_restricted_with_degree_lt,
                "incomplete_extension_in_fingers_lt": fl.incomplete_extension_in_fingers_lt,
                "varus_valgus_deformity_lt": fl.varus_valgus_deformity_lt,
                "b_l_knee_crept_with_gradation_lt": fl.b_l_knee_crept_with_gradation_lt,
                "pain_while_walking_reduced_by_lt": fl.pain_while_walking_reduced_by_lt,
                "b_l_slr_with_degree_lt": fl.b_l_slr_with_degree_lt,
                "varicose_veins_gradation_lt": fl.varicose_veins_gradation_lt,
                "burning_sensation_in_b_l_knee_shin_lt": fl.burning_sensation_in_b_l_knee_shin_lt,
                "morning_stiffness_with_duration_rt": fl.morning_stiffness_with_duration_rt,
                "b_l_shin_tenderness_with_gradation_rt": fl.b_l_shin_tenderness_with_gradation_rt,
                "b_l_knee_tenderness_with_gradation_and_position_rt": fl.b_l_knee_tenderness_with_gradation_and_position_rt,
                "b_l_shin_oedema_with_gradation_rt": fl.b_l_shin_oedema_with_gradation_rt,
                "pitting_oedema_rt": fl.pitting_oedema_rt,
                "non_pitting_oedema_rt": fl.non_pitting_oedema_rt,
                "b_l_shin_discoloration_rashes_itching_bruises_rt": fl.b_l_shin_discoloration_rashes_itching_bruises_rt,
                "local_knee_temperature_rt": fl.local_knee_temperature_rt,
                "heavy_free_rt": fl.heavy_free_rt,
                "complete_restricted_with_degree_rt": fl.complete_restricted_with_degree_rt,
                "incomplete_extension_in_fingers_rt": fl.incomplete_extension_in_fingers_rt,
                "varus_valgus_deformity_rt": fl.varus_valgus_deformity_rt,
                "b_l_knee_crept_with_gradation_rt": fl.b_l_knee_crept_with_gradation_rt,
                "pain_while_walking_reduced_by_rt": fl.pain_while_walking_reduced_by_rt,
                "b_l_slr_with_degree_rt": fl.b_l_slr_with_degree_rt,
                "varicose_veins_gradation_rt": fl.varicose_veins_gradation_rt,
                "burning_sensation_in_b_l_knee_shin_rt": fl.burning_sensation_in_b_l_knee_shin_rt,
                "others_s": fl.others_s,
                "jivha": fl.jivha,
                "jwaranubhuti": fl.jwaranubhuti,
                "kshudha": fl.kshudha,
                "kantha": fl.kantha,
                "tiktamlodgar": fl.tiktamlodgar,
                "mala_aadhman_malabaddhata_sticky_drava": fl.mala_aadhman_malabaddhata_sticky_drava,
                "mutra_naktamutrata_mutradaha": fl.mutra_naktamutrata_mutradaha,
                "rasa_dhatu_dushti_lakshane": fl.rasa_dhatu_dushti_lakshane,
                "nidra": fl.nidra,
                "sweda": fl.sweda,
                "others_k": fl.others_k,
            })

        # Group Attachment by date

        for att in attachment:
            if not att.attachment_date:
                continue

            if isinstance(att.attachment_date, datetime):
                date_key = att.attachment_date.date()

            else:
                date_key = att.attachment_date

            grouped_data[date_key]["attachment"].append({
                "admin": att.admin.name,
                "modified_by": att.write_uid.name,
                "file_type": att.file_type,
                "s3_url": att.s3_url,
                "other_description": att.other_description,
            })

        # Group X-Ray by date

        for xr in xray:
            if not xr.date_taken:
                continue
            if isinstance(xr.date_taken, datetime):
                date_key = xr.date_taken.date()
            else:
                date_key = xr.date_taken

            grouped_data[date_key]["xray"].append({
                "doctor_id": xr.doctor_id.name,
                "modified_by": xr.write_uid.name,
                "x_ray_day": xr.x_ray_day,
                "grade": xr.grade,
            })

        # Group Blood Report by date

        for br in blood_report:
            if not br.blood_report_date:
                continue
            if isinstance(br.blood_report_date, datetime):
                date_key = br.blood_report_date.date()
            else:
                date_key = br.blood_report_date

            grouped_data[date_key]["blood_report"].append({
                "doctor_id": br.doctor_id.name,
                "modified_by": br.write_uid.name,
                "blood_report_day": br.blood_report_day,
                "haemoglobin": br.haemoglobin,
                "esr": br.esr,
                "platelet_count": br.platelet_count,
                "bsl_fasting": br.bsl_fasting,
                "bsl_post_prandial": br.bsl_post_prandial,
                "hba1c": br.hba1c,
                "sr_uric_acid": br.sr_uric_acid,
                "ra_factor_titre": br.ra_factor_titre,
                "crp": br.crp,
                "ana": br.ana,
                "t_cholesterol": br.t_cholesterol,
                "t_triglyceride": br.t_triglyceride,
                "sr_creatinine": br.sr_creatinine,
                "tsh": br.tsh,
                "urine_sugar": br.urine_sugar,
                "urine_pus_cells_bacteria": br.urine_pus_cells_bacteria,
                "urine_protein": br.urine_protein,
                "urine_blood_crystal": br.urine_blood_crystal,
                "cbc": br.cbc,
                "lft": br.lft,
                "rft": br.rft,
                "lipid_profile": br.lipid_profile,
                "t3": br.t3,
                "t4": br.t4,
                "uric_acid": br.uric_acid,
                "ra": br.ra,
                "anti_ccp": br.anti_ccp,
                "homa_ir": br.homa_ir,
                "c_peptide": br.c_peptide,
                "la_ma_test": br.la_ma_test,
                "urine_routine": br.urine_routine,
                "urine_microscopic": br.urine_microscopic,
                "notes": br.notes,
            })

        # Build case papers list
        case_papers = []
        for date, details in grouped_data.items():
            case_papers.append({
                "date": date,
                "prescriptions": details["prescriptions"],
                "case_taking": details["case_taking"],
                "session": details["session"],
                "enrollment": details["enrollment"],
                "daily_followup": details["daily_followup"],
                "diet_chart": details["diet_chart"],
                "followup": details["followup"],
                "attachment": details["attachment"],
                "xray": details["xray"],
                "blood_report": details["blood_report"],
            })

        case_papers = sorted(case_papers, key=lambda x: x["date"], reverse=True)

        for cp in case_papers:
            cp["date"] = cp["date"].strftime("%d-%m-%Y")

        return request.render("web_management.custom_qweb_template", {
            "patient": patient,
            "case_papers": case_papers,
        })

    @http.route('/patient/xray/<string:uuid>', type='http', auth='user', website=True)
    def patient_xray_details(self, uuid, **kwargs):
        patient = request.env['clinic.patient'].sudo().search([("uuid", "=", uuid)], limit=1)
        if not patient:
            return request.not_found()

        attachment = request.env["patient.attachment"].sudo().search(
            [("patient_id", "=", patient.id), ("file_type", "=", "xray")],
            order="attachment_date desc"
        )

        grouped_data = defaultdict(lambda: {"attachment": []})

        for att in attachment:
            if not att.attachment_date:
                continue

            if isinstance(att.attachment_date, datetime):
                date_key = att.attachment_date.date()

            else:
                date_key = att.attachment_date

            grouped_data[date_key]["attachment"].append({
                "admin": att.admin.name,
                "modified_by": att.write_uid.name,
                "file_type": att.file_type,
                "s3_url": att.s3_url,
                "other_description": att.other_description,
            })

        x_rays_data = []
        for date, details in grouped_data.items():
            x_rays_data.append({
                "date": date,
                "attachment": details["attachment"]
            })

        x_rays_data = sorted(x_rays_data, key=lambda x: x["date"], reverse=True)

        for xrd in x_rays_data:
            xrd["date"] = xrd["date"].strftime("%d-%m-%Y")

        return request.render("web_management.custom_qweb_xray_template", {
            "patient": patient,
            "x_rays_data": x_rays_data,
        })