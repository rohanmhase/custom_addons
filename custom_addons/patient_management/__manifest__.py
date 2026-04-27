{
    "name": "Patient Management",
    "version": "1.0",
    "category": "Healthcare",
    "summary": "Manage patients in hospital",
    "description": """
        Module to manage patients:
        - Create patients
        - Store patient details
    """,
    "author": "Researchayu",
    "depends": ["base", "clinic_management", "product", "stock", "point_of_sale", "bus", "mail"],
    'data': [
        'data/ir_sequence_data.xml',
        'data/gradation_organ_data.xml',
        'data/cron.xml',
        'security/patient_security.xml',
        'security/ir.model.access.csv',
        'views/patient_views.xml',
        'views/blood_report_views.xml',
        'views/case_taking_views.xml',
        'views/daily_followup_views.xml',
        'views/diet_chart_views.xml',
        'views/followup_views.xml',
        'views/enrollment_views.xml',
        'views/session_views.xml',
        'views/xray_views.xml',
        'views/prescription_views.xml',
        'views/attachment_views.xml',
        'views/rs_followup_views.xml',
    ],
    'assets': {
        'web.assets_backend': [
            'patient_management/static/src/css/form.css',
            'patient_management/static/src/js/prescription_form_controller.js',
            'patient_management/static/src/css/prescription_styles.css',

        ],
        'point_of_sale._assets_pos': [
            "patient_management/static/src/js/prescription_popup.js",
            "patient_management/static/src/js/prescription_button.js",
            "patient_management/static/src/js/order_patch.js",
            "patient_management/static/src/js/restrict_product_click.js",
            "patient_management/static/src/js/orderline_patch.js",
            "patient_management/static/src/css/prescription_pos.css",
            "patient_management/static/src/js/pos_bus.js",
            "patient_management/static/src/xml/prescription_popup.xml",
            "patient_management/static/src/xml/prescription_button.xml",
        ],
    },
    "installable": True,
    "application": False,
    "license": "LGPL-3",
}
