{
    'name': 'Patient Experience Tracker',
    'version': '17.0.1.0.0',
    'category': 'Sales',
    'summary': 'Dedicated workspace for Pre-Sales to track patient follow-ups.',
    'depends': ['base', 'mail','patient_management'], # mail for chatter box
    'data': [
        'security/ir.model.access.csv',
        'views/experience_tracker_views.xml',
    ],
    'application': True, # This tells Odoo to put a big App Icon on the main dashboard
    'installable': True,
    'license': 'LGPL-3',
}