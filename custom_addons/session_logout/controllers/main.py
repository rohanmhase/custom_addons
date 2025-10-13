from odoo import http
from odoo.http import request
import logging

_logger = logging.getLogger(__name__)


class SessionController(http.Controller):

    @http.route('/web/session/check', type='json', auth='user')
    def check_session(self):
        """Check if session is still valid"""
        return {'status': 'active'}

    @http.route('/web/session/extend', type='json', auth='user')
    def extend_session(self):
        """Extend the current session"""
        if request.session.uid:
            # Update session last activity
            request.session.touch()
            return {'status': 'extended'}
        return {'status': 'expired'}
