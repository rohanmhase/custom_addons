{
    'name': 'PET_BM Tracker',
    'version': '17.0.1.0.0',
    'summary': 'Patient Experience Tracker and Business Manager Follow-ups',
    'depends': ['base','patient_management', 'mail'],
    'data': [
        'security/ir.model.access.csv',
        'data/pet_matrix.xml',
        'data/pet_cron.xml',
        'views/pet_bm_views.xml',
        'views/patient_inherit_views.xml',
    ],
    'installable': True,
    'application': True,
    "license": "LGPL-3",
}
