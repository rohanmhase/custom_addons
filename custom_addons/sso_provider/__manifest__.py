{
    'name': 'OAuth2 Identity Provider',
    'version': '17.0.1.0.0',
    'summary': 'Turns Odoo into an Identity Provider for ResearchAyu',
    'category': 'Tools',
    'author': 'Parikshit Hiwase',
    'depends': ['base', 'web'],
    'data': [
        'security/ir.model.access.csv',
        'views/oauth_client_views.xml',
    ],
    'installable': True,
    'application': False,
    'license': 'LGPL-3',
}