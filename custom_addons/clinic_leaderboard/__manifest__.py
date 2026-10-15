{
    'name': 'Clinic Leaderboard',
    'version': '1.0',
    'category': 'Healthcare',
    'summary': 'Dynamic department-wise performance leaderboard integrated with Metabase',
    'depends': ['base', 'web', 'hr'],
    'data': [
        'security/security.xml',
        'security/ir.model.access.csv',
        'data/cron_jobs.xml',
    ],
    'assets': {
        'web.assets_backend': [
            'clinic_leaderboard/static/src/js/clinic_leaderboard_banner.js',
            'clinic_leaderboard/static/src/xml/clinic_leaderboard_banner.xml',
        ],
    },
    'installable': True,
    'application': False,
}