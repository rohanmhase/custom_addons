import json
import logging
from odoo import http, fields
from odoo.http import request

_logger = logging.getLogger(__name__)


class OAuthProvider(http.Controller):

    @http.route('/oauth2/authorize', type='http', auth='user', website=False, sitemap=False)
    def authorize(self, **kwargs):
        """
        Step 1: User arrives from Moodle. Odoo forces login (auth='user').
        """
        client_id = kwargs.get('client_id')
        redirect_uri = kwargs.get('redirect_uri')
        state = kwargs.get('state')

        # Validate client exists
        client = request.env['oauth.client'].sudo().search([('client_id', '=', client_id)], limit=1)

        # SECURITY CHECK: Normalize URIs by stripping trailing slashes for comparison
        if not client or not redirect_uri or redirect_uri.rstrip('/') != client.redirect_uri.rstrip('/'):
            _logger.error(
                f"OAuth Redirect Mismatch: Got {redirect_uri}, Expected {client.redirect_uri if client else 'None'}")
            return f"Error: Invalid client_id or redirect_uri. Odoo expected {client.redirect_uri if client else 'a valid record'}."

        # Create the authorization code linked to the current user
        auth_code = request.env['oauth.code'].sudo().create({
            'user_id': request.uid,
            'client_id': client.id,
        })

        _logger.info(f"OAuth: Code generated for user {request.uid}, redirecting to {redirect_uri}")

        # THE FIX: local=False prevents Odoo from hijacking the redirect to its own domain.
        return request.redirect(f"{redirect_uri}?code={auth_code.code}&state={state}", local=False)

    @http.route('/oauth2/token', type='http', auth='none', methods=['POST'], csrf=False)
    def token(self, **kwargs):
        """
        Step 2: Moodle server swaps the code for a token.
        """
        code = kwargs.get('code')
        client_id = kwargs.get('client_id')
        client_secret = kwargs.get('client_secret')

        # Fallback for JSON payloads just in case Moodle sends raw JSON
        if not code and request.httprequest.data:
            try:
                payload = json.loads(request.httprequest.data)
                code = payload.get('code')
                client_id = payload.get('client_id')
                client_secret = payload.get('client_secret')
            except Exception:
                pass

        client = request.env['oauth.client'].sudo().search([
            ('client_id', '=', client_id),
            ('client_secret', '=', client_secret)
        ], limit=1)

        if not client:
            return request.make_response(json.dumps({'error': 'invalid_client'}),
                                         headers=[('Content-Type', 'application/json')])

        auth_code = request.env['oauth.code'].sudo().search([
            ('code', '=', code),
            ('client_id', '=', client.id)
        ], limit=1)

        if not auth_code:
            return request.make_response(json.dumps({'error': 'invalid_grant'}),
                                         headers=[('Content-Type', 'application/json')])

        response_data = {
            "access_token": auth_code.code,
            "token_type": "Bearer",
            "expires_in": 3600
        }
        return request.make_response(json.dumps(response_data),
                                     headers=[('Content-Type', 'application/json')])

    @http.route('/oauth2/userinfo', type='http', auth='none', methods=['GET', 'POST'], csrf=False)
    def userinfo(self, **kwargs):
        """
        Step 3: Moodle fetches user details.
        """
        auth_header = request.httprequest.headers.get('Authorization', '')
        access_token = auth_header.replace('Bearer ', '')

        auth_code = request.env['oauth.code'].sudo().search([('code', '=', access_token)], limit=1)

        if not auth_code:
            return request.make_response(json.dumps({'error': 'invalid_token'}),
                                         headers=[('Content-Type', 'application/json')])

        user = auth_code.user_id

        # Data that Moodle will use to create the account automatically
        response_data = {
            "sub": str(user.id),
            "name": user.name,
            "email": user.login,  # In Odoo, login is usually the email address
            "preferred_username": user.login,
        }

        _logger.info(f"OAuth: Sending userinfo for {user.login}")

        return request.make_response(json.dumps(response_data),
                                     headers=[('Content-Type', 'application/json')])