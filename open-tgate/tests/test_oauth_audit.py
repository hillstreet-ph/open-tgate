"""MCP OAuth consent, PKCE bindings, discovery and bounded audit regressions."""
import base64
import hashlib
import uuid
from unittest.mock import Mock

import pytest
from fastapi.testclient import TestClient

from app import api_workspace, oauth, workspace
from app.config import Settings
from app.main import app

client = TestClient(app)
CLIENT = str(uuid.uuid4())
OWNER = workspace.Principal(str(uuid.uuid4()), 'operator@example.test', operator=True)
VERIFIER = 'a' * 43
CHALLENGE = base64.urlsafe_b64encode(hashlib.sha256(VERIFIER.encode()).digest()).rstrip(b'=').decode()
REDIRECT = 'https://chatgpt.com/connector/oauth/test'
TARGET = 'https://open-tgate.site/mcp'


@pytest.fixture(autouse=True)
def setup(monkeypatch):
    app.dependency_overrides.clear()
    monkeypatch.setattr(oauth, 'get_settings', lambda: Settings(dashboard_origin='https://open-tgate.site'))
    yield
    app.dependency_overrides.clear()


def request_body(**extra):
    return {'client_id': CLIENT, 'redirect_uri': REDIRECT, 'resource': TARGET,
            'code_challenge': CHALLENGE, 'code_challenge_method': 'S256',
            'response_type': 'code', 'state': 'preserved state', 'scope': 'read', **extra}


def test_public_discovery_and_missing_token_challenge():
    metadata = client.get('/.well-known/oauth-protected-resource/mcp').json()
    assert metadata['resource'] == TARGET
    assert metadata['authorization_servers'] == ['https://open-tgate.site']
    assert client.get('/.well-known/oauth-authorization-server').json()['code_challenge_methods_supported'] == ['S256']
    response = client.post('/mcp', json={'jsonrpc': '2.0', 'id': 1, 'method': 'tools/list'})
    assert response.status_code == 401
    assert 'resource_metadata=' in response.headers['www-authenticate']


@pytest.mark.parametrize('uri', ['http://chatgpt.com/callback', 'https://evil.test/callback',
    'https://user:password@chatgpt.com/callback', REDIRECT + '#fragment',
    'https://chatgpt.com:8443/callback', 'https://chatgpt.com:bad/callback'])
def test_registration_rejects_redirects_before_storage(monkeypatch, uri):
    storage = Mock()
    monkeypatch.setattr(oauth, 'rest', storage)
    assert client.post('/oauth/register', json={'redirect_uris': [uri]}).status_code == 400
    storage.assert_not_called()


def test_registration_no_secret_public_client(monkeypatch):
    storage = Mock(return_value=True)
    monkeypatch.setattr(oauth, 'rest', storage)
    response = client.post('/oauth/register', json={'client_name': 'ChatGPT', 'redirect_uris': [REDIRECT]})
    assert response.status_code == 201
    assert response.json()['token_endpoint_auth_method'] == 'none'
    assert 'client_secret' not in response.json()
    assert response.headers['cache-control'] == 'no-store'


@pytest.mark.parametrize('uri', [
    'https://backend.composio.dev/api/v1/auth-apps/add',
    'https://backend.composio.dev/api/v3/toolkits/auth/callback',
])
def test_registration_accepts_documented_composio_callbacks(monkeypatch, uri):
    storage = Mock(return_value=True)
    monkeypatch.setattr(oauth, 'rest', storage)
    response = client.post('/oauth/register', json={
        'client_name': 'Composio', 'redirect_uris': [uri],
        'token_endpoint_auth_method': 'none',
    })
    assert response.status_code == 201
    assert response.json()['redirect_uris'] == [uri]
    assert response.json()['token_endpoint_auth_method'] == 'none'
    assert 'client_secret' not in response.json()
    assert storage.call_args.args == ('POST', 'rpc/open_tgate_register_oauth_client')
    assert storage.call_args.kwargs['body']['redirects'] == [uri]


@pytest.mark.parametrize('uri', [
    'http://backend.composio.dev/api/v1/auth-apps/add',
    'https://backend.composio.dev.evil.test/api/v1/auth-apps/add',
    'https://attacker.backend.composio.dev/api/v1/auth-apps/add',
    'https://user:password@backend.composio.dev/api/v1/auth-apps/add',
    'https://backend.composio.dev:8443/api/v1/auth-apps/add',
    'https://backend.composio.dev/api/v1/auth-apps/add#fragment',
])
def test_registration_rejects_unsafe_composio_callback_variants(monkeypatch, uri):
    storage = Mock()
    monkeypatch.setattr(oauth, 'rest', storage)
    assert client.post('/oauth/register', json={'redirect_uris': [uri]}).status_code == 400
    storage.assert_not_called()


def test_explicit_redirect_hosts_override_composio_default(monkeypatch):
    monkeypatch.setattr(oauth, 'get_settings', lambda: Settings(
        dashboard_origin='https://open-tgate.site', oauth_redirect_hosts='chatgpt.com'))
    storage = Mock()
    monkeypatch.setattr(oauth, 'rest', storage)
    response = client.post('/oauth/register', json={
        'redirect_uris': ['https://backend.composio.dev/api/v1/auth-apps/add']})
    assert response.status_code == 400
    storage.assert_not_called()


@pytest.mark.parametrize('extra', [{'resource': 'https://evil.test/mcp'}, {'scope': 'send'},
    {'redirect_uri': 'https://chatgpt.com/wrong'}, {'code_challenge_method': 'plain'}])
def test_authorize_rejects_bad_target_scope_redirect_pkce(monkeypatch, extra):
    monkeypatch.setattr(oauth, 'rest', Mock(return_value=[{'redirect_uris': [REDIRECT], 'client_name': 'ChatGPT'}]))
    assert client.get('/oauth/authorize', params=request_body(**extra), follow_redirects=False).status_code == 400


def test_authorization_requires_operator_and_explicit_consent(monkeypatch):
    storage = Mock(return_value=[{'redirect_uris': [REDIRECT], 'client_name': 'ChatGPT'}])
    monkeypatch.setattr(oauth, 'rest', storage)
    result = client.get('/oauth/authorize', params=request_body(), follow_redirects=False)
    assert result.status_code == 307
    assert '/app?oauth_request=' in result.headers['location']
    assert storage.call_count == 1  # No code is created by GET.
    assert client.post('/oauth/approve', json=request_body()).status_code == 401
    app.dependency_overrides[workspace.require_operator] = lambda: OWNER
    result = client.post('/oauth/approve', json=request_body()).json()
    inserted = storage.call_args.kwargs['body']
    assert inserted['created_by'] == OWNER.user_id
    assert 'code=' in result['redirect_uri'] and 'state=preserved+state' in result['redirect_uri']
    assert 'code_hash' in inserted and 'code' not in inserted
    storage.reset_mock()
    assert 'error=access_denied' in client.post('/oauth/deny', json=request_body()).json()['redirect_uri']
    assert storage.call_count == 1  # Denial never mints a code.


def test_token_exchange_pkce_and_resource_passed_to_atomic_rpc(monkeypatch):
    storage = Mock(return_value={'scopes': ['read']})
    monkeypatch.setattr(oauth, 'rest', storage)
    response = client.post('/oauth/token', data={'grant_type': 'authorization_code', 'client_id': CLIENT,
         'resource': TARGET, 'code': 'single-use-code', 'code_verifier': VERIFIER, 'redirect_uri': REDIRECT})
    assert response.status_code == 200
    args = storage.call_args.kwargs['body']
    assert args['pkce'] == CHALLENGE and args['target'] == TARGET
    assert args['code_digest'] == workspace.key_hash('single-use-code')
    assert args['access_hash'] == workspace.key_hash(response.json()['access_token'])
    assert args['refresh_hash'] == workspace.key_hash(response.json()['refresh_token'])
    assert 'single-use-code' not in str(args)
    assert response.json()['expires_in'] == 3600
    assert client.post('/oauth/token', data={'resource':'wrong'}).json()['error'] == 'invalid_target'
    storage.return_value = None
    assert client.post('/oauth/token', data={'grant_type': 'authorization_code', 'client_id': CLIENT,
         'resource': TARGET, 'code': 'single-use-code', 'code_verifier': VERIFIER, 'redirect_uri': REDIRECT}).status_code == 400


def test_token_invalid_resource_duplicate_field_and_oversized_body(monkeypatch):
    storage = Mock()
    monkeypatch.setattr(oauth, 'rest', storage)
    assert client.post('/oauth/token', data={'resource': 'wrong'}).status_code == 400
    assert client.post('/oauth/token', content='client_id=one&client_id=two', headers={'content-type':'application/x-www-form-urlencoded'}).status_code == 400
    assert client.post('/oauth/token', content='x='+'a'*17000, headers={'content-type':'application/x-www-form-urlencoded'}).status_code == 413
    storage.assert_not_called()


def test_refresh_rotates_hash_and_preserves_target_client_binding(monkeypatch):
    storage = Mock(return_value={'scopes': ['read']})
    monkeypatch.setattr(oauth, 'rest', storage)
    response = client.post('/oauth/token', data={'grant_type':'refresh_token','client_id':CLIENT,'resource':TARGET,'refresh_token':'old-refresh'})
    assert response.status_code == 200
    assert storage.call_args.args[1] == 'rpc/open_tgate_refresh_oauth_token'
    assert storage.call_args.kwargs['body']['previous_hash'] == workspace.key_hash('old-refresh')
    assert storage.call_args.kwargs['body']['client'] == CLIENT


def test_audit_and_deleted_tools_are_scoped_bounded_and_ids_are_text(monkeypatch):
    app.dependency_overrides[workspace.require_reader] = lambda: OWNER
    storage = Mock(return_value=[])
    monkeypatch.setattr(api_workspace, 'rest', storage)
    assert client.get('/api/v1/workspace/audit?before=9007199254740993&limit=200').status_code == 200
    args = storage.call_args.kwargs['params']
    assert args['id'] == 'lt.9007199254740993' and 'id::text' in args['select']
    assert client.get('/api/v1/workspace/deleted?limit=201').status_code == 422
    assert client.get('/api/v1/workspace/deleted').status_code == 200
    assert storage.call_args.kwargs['params']['deleted'] == 'eq.true'
    tools = client.post('/mcp', json={'jsonrpc':'2.0','id':1,'method':'tools/list'}).json()['result']['tools']
    assert {'list_audit_events','get_deleted_messages'} <= {t['name'] for t in tools}
    assert all(t['securitySchemes'][0]['type']=='oauth2' for t in tools)
    app.dependency_overrides[workspace.require_reader] = lambda: workspace.Principal(OWNER.user_id, scopes=frozenset({'knowledge:read'}))
    assert client.get('/api/v1/workspace/audit').status_code == 403


def test_registration_capacity_returns_retryable_error(monkeypatch):
    storage = Mock(return_value=False)
    monkeypatch.setattr(oauth, 'rest', storage)
    response = client.post('/oauth/register', json={'redirect_uris': [REDIRECT]})
    assert response.status_code == 429
    assert response.headers['retry-after'] == '60'
    assert response.headers['cache-control'] == 'no-store'
    assert response.json()['error'] == 'registration_rate_limited'


def test_plugin_declares_dynamic_oauth_registration():
    import json
    from pathlib import Path
    config = json.loads((Path(__file__).parents[1] / 'plugin/open-tgate/mcp.json').read_text())
    auth = config['mcpServers']['open-tgate']['extensions']['com.openai']['auth']
    assert auth == {'type': 'oauth', 'client': {'mode': 'dcr'}}
