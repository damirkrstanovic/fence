import flask
import json

from fence.auth import login_required
from fence.data_model.models import GoogleServiceAccount
from fence.data_model.models import GoogleProxyGroup
from fence.data_model.models import UserRefreshToken
from fence.errors import NotSupported, UserError
from fence.jwt.token import USER_ALLOWED_SCOPES
from fence.resources.storage.cdis_jwt import create_refresh_token,\
    revoke_refresh_token, create_user_access_token

from flask import current_app as capp
from flask import g, request, jsonify
from ..resources.storage import get_endpoints_descriptions
from flask import abort
from flask_sqlalchemy_session import current_session

from cirrus import GoogleCloudManager


blueprint = flask.Blueprint('credentials', __name__)

ALL_RESOURCES = {
    '/cdis': 'access to CDIS APIs',
    '/ceph': 'access to Ceph storage',
    '/cleversafe': 'access to cleversafe storage',
    '/aws-s3': 'access to AWS S3 storage',
    '/google': 'access to Google storage'
}


@blueprint.route('/', methods=['GET'])
@login_required({'credentials'})
def list_sources():
    '''
    List different resources user can have credentials

    **Example:**
    .. code-block:: http

           GET /credentials/ HTTP/1.1
           Content-Type: application/json
           Accept: application/json

    .. code-block:: JavaScript

        {
            "/cdis": "access to CDIS APIs",
            "/ceph": "access to Ceph storage",
            "/cleversafe": "access to cleversafe storage",
            "/aws-s3", "access to AWS S3 storage"
            "/google", "access to Google Cloud storage"
        }
    '''
    services = capp.config.get('STORAGES', [])
    return jsonify(get_endpoints_descriptions(services, current_session))


@blueprint.route('/<provider>/', methods=['GET'])
@login_required({'credentials'})
def list_keypairs(provider):
    '''
    List access keys for user

    **Example:**
    .. code-block:: http

           POST /credentials/apis/ HTTP/1.1
           Content-Type: application/json
           Accept: application/json


    google:
    Info from Google API /serviceAccounts/<account>/keys endpoint
    TODO: In the future we should probably add in our expiration time, when
          we start monitoring and deleting after x amount of time

    .. code-block:: JavaScript

        {
            "access_keys":
            [
                {
                    "keyAlgorithm": enum(ServiceAccountKeyAlgorithm),
                    "validBeforeTime": string,
                    "name": string,
                    "validAfterTime": string,
                },
                ...
            ]
        }

    other:

    .. code-block:: JavaScript
        cdis
        {
            "jtis":
            [
               {"jti": "e9d58890-99b0-44f0-88bd-3ebc370b1329", "exp": 12345678},
               {"jti": "e9d58890-99b0-44f0-88bd-3ebc370b132a", "exp": 17245678}
            ]
        }
        non-cdis
        {
            "access_keys":
            [
                {
                    "access_key": "8DGW9LyC0D4nByoWo6pp",
                }
            ]
        }

    '''
    if provider == 'cdis':
        with capp.db.session as session:
            tokens = session.query(UserRefreshToken) \
                .filter_by(userid=g.user.id).order_by(UserRefreshToken.expires.desc())\
                .all()
            result = {
                'jtis':
                    [{'jti': item.jti, 'exp': item.expires} for item in tokens]}
    elif provider == 'google':
        with GoogleCloudManager() as g_cloud_manager:
            service_account = _get_google_service_account_for_client(g_cloud_manager)

            if service_account:
                keys = g_cloud_manager.get_service_account_keys_info(service_account.google_unique_id)
                result = {'access_keys': keys}
            else:
                result = {'access_keys': []}
    else:
        result = capp.storage_manager.list_keypairs(provider, g.user)
        keys = {
            'access_keys':
                [{'access_key': item['access_key']} for item in result]}
        result = keys
    return jsonify(result)


@blueprint.route('/<provider>/', methods=['POST'])
@login_required({'credentials'})
def create_keypairs(provider):
    '''
    Generate a keypair for user

    :query expires_in: expiration time in seconds, default and max is 30 days

    **Example:**
    .. code-block:: http

           POST /credentials/cdis/?expires_in=3600 HTTP/1.1
           Content-Type: application/json
           Accept: application/json

    cdis:

    .. code-block:: JavaScript

        {
            "key_id": result,
            "api_key": result
        }

    google:
    (JSON key in Google Credentials File format)

    .. code-block:: JavaScript

        {
            "type": "service_account",
            "project_id": "project-id",
            "private_key_id": "some_number",
            "private_key": "-----BEGIN PRIVATE KEY-----\n....
            =\n-----END PRIVATE KEY-----\n",
            "client_email": "<api-name>api@project-id.iam.gserviceaccount.com",
            "client_id": "...",
            "auth_uri": "https://accounts.google.com/o/oauth2/auth",
            "token_uri": "https://accounts.google.com/o/oauth2/token",
            "auth_provider_x509_cert_url": "https://www.googleapis.com/oauth2/v1/certs",
            "client_x509_cert_url": "https://www.googleapis.com/...<api-name>api%40project-id.iam.gserviceaccount.com"
        }

    other:

    .. code-block:: JavaScript

        {
            "access_key": "8DGW9LyC0D4nByoWo6pp",
            "secret_key": "1lnkGScEH8Vr4EC6QnoqLK1PqRWPNqIBJkH6Vpgx"
        }
    '''
    client_id = getattr(g, 'client_id', None)
    if provider == 'cdis':
        # requestor is user if client_id is not set
        client_id = str(g.user.id)
        if flask.request.headers.get('Content-Type') == 'application/x-www-form-urlencoded':
            scopes = request.form.getlist("scope")
        else:
            try:
                scopes = json.loads(request.data).get("scope") or []
            except ValueError:
                scopes = []
        if not isinstance(scopes, list):
            scopes = scopes.split(',')
        for scope in scopes:
            if scope not in USER_ALLOWED_SCOPES:
                raise NotSupported('Scope {} is not supported'.format(scope))
        expires_in = min(int(request.args.get('expires_in', 2592000)), 2592000)
        token, jti = create_refresh_token(
            g.user, capp.keypairs[0],
            expires_in,
            scopes, client_id
        )
        return jsonify(dict(key_id=jti, api_key=token))
    elif provider == 'google':
        with GoogleCloudManager() as g_cloud:
            key = _get_google_access_key(g_cloud)
        return jsonify(key)
    else:
        return jsonify(capp.storage_manager.create_keypair(provider, g.user))


@blueprint.route('/cdis/access_token', methods=['POST'])
def create_access_token_api():
    '''
    Generate an access_token for user

    :query expires_in: expiration time in seconds, default to 3600, max is 3600

    **Example:**
    .. code-block:: http

           POST /hmac/ HTTP/1.1
           Content-Type: application/json
           Accept: application/json


    .. code-block:: JavaScript

        {
            "token" "token_value"
        }
    '''
    if flask.request.headers.get('Content-Type') == 'application/x-www-form-urlencoded':
        api_key = request.form.get("api_key")
    else:
        try:
            api_key = json.loads(request.data).get("api_key")
        except ValueError:
            api_key = None
    if not api_key:
        raise UserError("Please provide an api_key in payload")
    expires_in = min(int(request.args.get('expires_in', 3600)), 3600)
    result = create_user_access_token(
        capp.keypairs[0], api_key, expires_in)
    return jsonify(dict(access_token=result))


@blueprint.route('/<provider>/<access_key>', methods=['DELETE'])
@login_required({'credentials'})
def delete_keypair(provider, access_key):
    '''
    .. http:get: /<provider>/(string: access_key)
    Delete a keypair for user

    :param access_key: existing access key belongs to this user

    For Google:
        The access_key can be constructed from
        data in the response from creating the key.

        access_key should be a string:
        `projects/{PROJECT_ID}/serviceAccounts/{ACCOUNT}/keys/{KEY}`
        which can be constructed with the information from POST/PUT `credentials/google`
        Use `project_id` for {PROJECT_ID},
            `client_email` for {ACCOUNT},
            `private_key_id` for {KEY}

    :statuscode 201 Success
    :statuscode 403 Forbidden to delete access key
    :statuscode 404 Access key doesn't exist

    '''
    if provider == 'cdis':
        params = json.loads(request.data)
        exp = params.get('exp')
        if exp is None:
            abort(400, 'No expires time passed')
        status = revoke_refresh_token(access_key, exp)
    elif provider == 'google':
        with GoogleCloudManager() as g_cloud:
            service_account = _get_google_service_account_for_client(g_cloud)

            if service_account:
                keys_for_account = (
                    g_cloud.get_service_account_keys_info(service_account.google_unique_id)
                )

                # Only delete the requested key if is owned by current client's SA
                if access_key in [key['name'].split('/')[-1] for key in keys_for_account]:
                    g_cloud.delete_service_account_key(service_account.google_unique_id,
                                                       access_key)
                    status = '', 200
                else:
                    abort(404, 'Could not delete key ' + access_key + '. Not found for current user.')
            else:
                abort(404, 'Could not find service account for current user.')
    else:
        capp.storage_manager.delete_keypair(provider, g.user, access_key)
        status = '', 200

    return status


def _get_google_access_key(g_cloud_manager):
    """
    Return an access key for current user and client.

    Args:
        g_cloud_manager (cirrus.GoogleCloudManager): instance of
        cloud manager to use

    Returns:

        JSON key in Google Credentials File format:

        .. code-block:: JavaScript

            {
                "type": "service_account",
                "project_id": "project-id",
                "private_key_id": "some_number",
                "private_key": "-----BEGIN PRIVATE KEY-----\n....
                =\n-----END PRIVATE KEY-----\n",
                "client_email": "<api-name>api@project-id.iam.gserviceaccount.com",
                "client_id": "...",
                "auth_uri": "https://accounts.google.com/o/oauth2/auth",
                "token_uri": "https://accounts.google.com/o/oauth2/token",
                "auth_provider_x509_cert_url": "https://www.googleapis.com/oauth2/v1/certs",
                "client_x509_cert_url": "https://www.googleapis.com/...<api-name>api%40project-id.iam.gserviceaccount.com"
            }
    """
    service_account = _get_google_service_account_for_client(g_cloud_manager)

    if not service_account:
        service_account = _create_google_service_account_for_client(g_cloud_manager)

    key = g_cloud_manager.get_access_key(service_account.google_unique_id)
    return key


def _get_google_service_account_for_client(g_cloud_manager):
    """
    Return the service account (from Fence db) for current client.

    Get the service account that is associated with the current client
    for this user. There will be a single service account per client.

    NOTE: The user themselves will also have a single service account
          which will be used when "client_id" is their user.id

    Args:
        g_cloud_manager (cirrus.GoogleCloudManager): instance of
        cloud manager to use

    Returns:
        fence.data_model.models.GoogleServiceAccount: Client's service account
    """
    client_id = getattr(g, 'client_id', None)
    service_account = (
        current_session
        .query(GoogleServiceAccount)
        .filter_by(client_id=client_id,
                   user_id=g.user.id)
        .first()
    )

    return service_account


def _create_google_service_account_for_client(g_cloud_manager):
    """
    Create a Google Service account for the current client and user.

    Args:
        g_cloud_manager (cirrus.GoogleCloudManager): instance of
        cloud manager to use

    Returns:
        fence.data_model.models.GoogleServiceAccount: New service account
    """
    # create service account, add to db
    proxy_group = (
        current_session
        .query(GoogleProxyGroup)
        .filter_by(user_id=g.user.id)
        .first()
    )

    if proxy_group:
        client_id = getattr(g, 'client_id', None)
        new_service_account = (
            g_cloud_manager.create_service_account_for_proxy_group(proxy_group.id,
                                                                   account_id=client_id)
        )

        service_account = GoogleServiceAccount(
            google_unique_id=new_service_account['uniqueId'],
            client_id=client_id,
            user_id=g.user.id,
            email=new_service_account['email']
        )
        current_session.add(service_account)
        current_session.commit()
    else:
        # TODO Should we create a group here if one doesn't exist for some reason?
        # These groups *should* get created during dpbap sync
        abort(404, 'Could not find Google proxy group for current user.')

    return service_account
