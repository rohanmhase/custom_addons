{
    'name': 'Delivery Challan Price',
    'version': '17.0.1.0.0',
    'summary': 'Add optional Unit Price, Subtotal & Grand Total to Delivery Orders',
    'description': """
        Adds optional price columns (Unit Price, Subtotal) to Delivery Orders.
        Also shows a Grand Total at the bottom when prices are entered.
        Columns can be toggled via the settings icon in the operations table.
        Prices also appear on the printed Delivery Slip PDF.
    """,
    'category': 'Inventory',
    'depends': ['stock'],
    'data': [
        'views/dc_price_views.xml',
        'views/dc_price_report.xml',
    ],
    'installable': True,
    'application': False,
    'license': 'LGPL-3',
}