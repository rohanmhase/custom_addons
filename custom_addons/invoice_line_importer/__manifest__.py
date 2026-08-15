# -*- coding: utf-8 -*-
{
    'name': 'Invoice Line Importer',
    'version': '17.0.1.0.0',
    'category': 'Accounting/Accounting',
    'summary': 'Import invoice lines from a CSV file into draft invoices',
    'description': """
Invoice Line Importer
=====================
Adds an Import Lines button on Customer Invoice and Vendor Bill form views.

Features:
  - Upload CSV to create invoice lines automatically
  - Product lookup by name or internal reference (case-insensitive)
  - Multiple taxes per line (comma-separated)
  - Appends to existing lines (non-destructive)
  - Sample CSV template download
  - Row-level error reporting
  - Draft invoices only

CSV Columns: Product(*), Quantity(*), Unit Price(*), Taxes, Description
(*) = required
    """,
    'author': 'Your Company',
    'website': 'https://www.yourcompany.com',
    'license': 'LGPL-3',
    'depends': ['account'],
    'data': [
        'wizards/import_lines_wizard_views.xml',
        'security/ir.model.access.csv',
        'views/account_move_views.xml',
    ],
    'installable': True,
    'application': False,
    'auto_install': False,
    'assets': {},
}