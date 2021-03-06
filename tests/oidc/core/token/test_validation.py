"""
Test the OP validation of the token request.

Spec from OIDC 3.1.3.2:

    The Authorization Server MUST validate the Token Request as follows:

    - Authenticate the Client if it was issued Client Credentials or if it uses
      another Client Authentication method, per Section 9.
    - Ensure the Authorization Code was issued to the authenticated Client.
    - Verify that the Authorization Code is valid.
    - If possible, verify that the Authorization Code has not been previously
      used.
    - Ensure that the redirect_uri parameter value is identical to the
      redirect_uri parameter value that was included in the initial
      Authorization Request. If the redirect_uri parameter value is not present
      when there is only one registered redirect_uri value, the Authorization
      Server MAY return an error (since the Client should have included the
      parameter) or MAY proceed without an error (since OAuth 2.0 permits the
      parameter to be omitted in this case).
    - Verify that the Authorization Code used was issued in response to an
      OpenID Connect Authentication Request (so that an ID Token will be
      returned from the Token Endpoint).

Tests for client authentication are in
``tests/oidc/core/token/test_client_authentication.py``.

The rest of these points have tests below.
"""

from authlib.common.security import generate_token

from tests.utils import oauth2


def test_reuse_code_invalid(client, oauth_client):
    """
    Test that an authorization code returned from the authorization endpoint
    can be used only once, and after that its attempted usage will return an
    ``invalid_request`` error.
    """
    code = oauth2.get_access_code(client, oauth_client)
    # Test that the first time using the code is fine.
    oauth2.check_token_response(oauth2.post_token(client, oauth_client, code))
    # Test that the second time using the code breaks.
    token_response = oauth2.post_token(client, oauth_client, code)
    assert token_response.status_code == 400
    assert 'error' in token_response.json
    assert token_response.json['error'] == 'invalid_request'


def test_different_client_invalid(client, oauth_client, oauth_client_B):
    """
    Test that one client cannot use an authorization code which was issued to a
    different client, and the request fails with ``invalid_request``.
    """
    code = oauth2.get_access_code(client, oauth_client)
    # Have client B send the code to the token endpoint.
    token_response = oauth2.post_token(client, oauth_client_B, code)
    assert token_response.status_code == 400
    assert 'error' in token_response.json
    assert token_response.json['error'] == 'invalid_request'


def test_invalid_code(client, oauth_client):
    """
    Test that a client can't just send in a garbage code.
    """
    code = generate_token(50)
    token_response = oauth2.post_token(client, oauth_client, code)
    assert token_response.status_code == 400
    assert 'error' in token_response.json
    assert token_response.json['error'] == 'invalid_request'


def test_invalid_redirect_uri(client, oauth_client):
    """
    Test that if the token request has a different redirect_uri than the one
    the client is suppsed to be using that an error is raised, with the
    ``invalid_request`` code.
    """
    code = oauth2.get_access_code(client, oauth_client)
    headers = oauth2.create_basic_header_for_client(oauth_client)
    wrong_redirect_uri = oauth_client.url + '/some-garbage'
    data = {
        'client_id': oauth_client.client_id,
        'client_secret': oauth_client.client_secret,
        'code': code,
        'grant_type': 'authorization_code',
        'redirect_uri': wrong_redirect_uri,
    }
    token_response = client.post('/oauth2/token', headers=headers, data=data)
    assert token_response.status_code == 400
    assert 'error' in token_response.json
    assert token_response.json['error'] == 'invalid_request'


def test_no_redirect_uri(client, oauth_client):
    """
    Test that if the token request has no ``redirect_uri`` that an error is
    raised, with the ``invalid_request`` code.
    """
    code = oauth2.get_access_code(client, oauth_client)
    headers = oauth2.create_basic_header_for_client(oauth_client)
    # Note no ``redirect_uri`` in the data.
    data = {
        'client_id': oauth_client.client_id,
        'client_secret': oauth_client.client_secret,
        'code': code,
        'grant_type': 'authorization_code',
    }
    token_response = client.post('/oauth2/token', headers=headers, data=data)
    assert token_response.status_code == 400
    assert 'error' in token_response.json
    assert token_response.json['error'] == 'invalid_request'
