import json
import logging
from odoo import http, fields
from odoo.http import request
import datetime

_logger = logging.getLogger(__name__)


class OAuthProvider(http.Controller):

    @http.route('/oauth2/authorize', type='http', auth='user', website=False, sitemap=False)
    def authorize(self, **kwargs):
        """
        Step 1: The user is redirected here from Moodle.
        Because auth='user', Odoo automatically forces them to log in first.
        """
        client_id = kwargs.get('client_id')
        redirect_uri = kwargs.get('redirect_uri')
        state = kwargs.get('state')

        # Validate the client exists and the redirect URI matches
        client = request.env['oauth.client'].sudo().search([('client_id', '=', client_id)], limit=1)

        # --- DIAGNOSTIC PRINT STATEMENTS ---
        print("\n" + "=" * 50)
        print(f"RECEIVED FROM URL -> ID: '{client_id}', URI: '{redirect_uri}'")
        if client:
            print(f"FOUND IN DATABASE -> ID: '{client.client_id}', URI: '{client.redirect_uri}'")
        else:
            print("FOUND IN DATABASE -> No client found with that exact ID!")
        print("=" * 50 + "\n")
        # -----------------------------------

        if not client or redirect_uri != client.redirect_uri:
            return "Error: Invalid client_id or redirect_uri. Please check Odoo settings."

        # Create a one-time use authorization code
        auth_code = request.env['oauth.code'].sudo().create({
            'user_id': request.uid,
            'client_id': client.id,
        })

        # Redirect back to Moodle with the code and the original state
        return request.redirect(f"{redirect_uri}?code={auth_code.code}&state={state}")

    @http.route('/oauth2/token', type='http', auth='none', methods=['POST'], csrf=False)
    def token(self, **kwargs):
        """
        Step 2: Moodle server calls this to swap the code for a token.
        (Updated to type='http' to return a flat REST JSON response)
        """
        # Moodle typically sends OAuth2 data as form-urlencoded, which populates kwargs
        code = kwargs.get('code')
        client_id = kwargs.get('client_id')
        client_secret = kwargs.get('client_secret')

        # Fallback just in case Moodle sends a raw JSON payload instead
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

        # Return standard flat JSON
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
        Step 3: Moodle asks who the user is using the access_token.
        (Updated to type='http' to return a flat REST JSON response)
        """
        auth_header = request.httprequest.headers.get('Authorization', '')
        access_token = auth_header.replace('Bearer ', '')

        auth_code = request.env['oauth.code'].sudo().search([('code', '=', access_token)], limit=1)

        if not auth_code:
            return request.make_response(json.dumps({'error': 'invalid_token'}),
                                         headers=[('Content-Type', 'application/json')])

        user = auth_code.user_id

        # Return standard flat JSON
        response_data = {
            "sub": str(user.id),
            "name": user.name,
            "email": user.login,
            "preferred_username": user.login,
        }
        return request.make_response(json.dumps(response_data),
                                     headers=[('Content-Type', 'application/json')])
