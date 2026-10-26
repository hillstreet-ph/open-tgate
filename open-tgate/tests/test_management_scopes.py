"""Opt-in management grants cannot cross operator, Telegram or grounding boundaries."""
import json
import uuid
from unittest.mock import Mock

import httpx
import pytest
from fastapi.testclient import TestClient

from app import api_workspace, oauth, workspace
from app.config import Settings
from app.main import app

client = TestClient(app)
OWNER = str(uuid.uuid4())
SOURCE = str(uuid.uuid4())
ACCOUNT = str(uuid.uuid4())


@pytest.fixture(autouse=True)
def overrides():
    app.dependency_overrides.clear()
    yield
    app.dependency_overrides.clear()


def grant(*scopes):
    principal = workspace.Principal(OWNER, scopes=frozenset(scopes))
    app.dependency_overrides[workspace.require_reader] = lambda: principal
    return principal


def call(name, arguments=None):
    return client.post('/mcp', json={'jsonrpc': '2.0', 'id': 1, 'method': 'tools/call',
                                   'params': {'name': name, 'arguments': arguments or {}}})


@pytest.mark.parametrize('scopes,expected', [
    (['read'], {'get_capabilities', 'get_history'}),
    (['knowledge:write'], {'get_capabilities', 'list_knowledge_sources', 'create_knowledge_source',
                           'update_knowledge_source', 'delete_knowledge_source'}),
    (['ai:draft'], {'get_capabilities', 'list_personas', 'create_persona', 'update_persona', 'delete_persona'}),
    (['ai:draft', 'knowledge:read'], {'get_capabilities', 'draft_reply', 'search_knowledge',
                                      'list_personas', 'create_persona', 'update_persona', 'delete_persona'}),
    (['accounts:write'], {'get_capabilities', 'rename_account'}),
])
def test_tools_discovery_uses_all_required_scopes(scopes, expected):
    grant(*scopes)
    tools = client.post('/mcp', json={'jsonrpc': '2.0', 'id': 1, 'method': 'tools/list'}).json()['result']['tools']
    names = {tool['name'] for tool in tools}
    if scopes == ['read']:
        assert expected <= names
        assert not names & api_workspace.MUTATION_TOOLS
    else:
        assert names == expected
    caps = client.get('/api/v1/workspace/capabilities').json()
    assert caps['scopes'] == sorted(scopes)
    assert caps['operator'] is False
    mcp_caps = json.loads(call('get_capabilities').json()['result']['content'][0]['text'])
    assert mcp_caps == caps
    assert 'telegram_send' in caps['unsupported_operations']
    assert set(caps['default_scopes']) == {'read', 'knowledge:read'}


@pytest.mark.parametrize('scopes', [[], ['read'], ['knowledge:read'], ['accounts:write'], ['ai:draft']])
def test_knowledge_management_denied_before_storage(scopes, monkeypatch):
    grant(*scopes)
    storage = Mock()
    monkeypatch.setattr(api_workspace, 'rest', storage)
    assert client.get('/api/v1/workspace/knowledge').status_code == 403
    assert client.post('/api/v1/workspace/knowledge', json={'title': 'T', 'content': 'C'}).status_code == 403
    assert client.patch('/api/v1/workspace/knowledge/' + SOURCE, json={'approved': True}).status_code == 403
    assert client.delete('/api/v1/workspace/knowledge/' + SOURCE + '?confirm=true').status_code == 403
    assert call('create_knowledge_source', {'title': 'T', 'content': 'C'}).status_code == 403
    storage.assert_not_called()


def test_knowledge_rest_and_mcp_share_validated_operations(monkeypatch):
    grant('knowledge:write')
    storage = Mock(return_value=[{'id': SOURCE, 'title': 'T'}])
    monkeypatch.setattr(api_workspace, 'rest', storage)
    created = call('create_knowledge_source', {'title': 'T', 'content': 'C'})
    assert created.json()['result']['isError'] is False
    assert storage.call_args.kwargs['body']['actor_user'] == OWNER
    assert storage.call_args.kwargs['body']['payload']['approved'] is False
    assert call('update_knowledge_source', {'source_id': SOURCE, 'content': 'Revised', 'approved': True}).json()['result']['isError'] is False
    assert storage.call_args.kwargs['body']['payload'] == {'content': 'Revised', 'approved': True}
    storage.reset_mock()
    assert client.delete('/api/v1/workspace/knowledge/' + SOURCE).status_code == 422
    assert call('delete_knowledge_source', {'source_id': SOURCE}).json()['error']['code'] == -32602
    assert call('delete_knowledge_source', {'source_id': SOURCE, 'confirm': False}).json()['error']['code'] == -32602
    storage.assert_not_called()
    assert call('delete_knowledge_source', {'source_id': SOURCE, 'confirm': True}).json()['result']['isError'] is False
    storage.assert_called_once_with('POST', 'rpc/open_tgate_mutate_workspace', body={
        'resource_name': 'knowledge', 'operation_name': 'DELETE', 'record_id': SOURCE,
        'payload': {}, 'actor_user': OWNER, 'actor_key': None})
    assert client.delete('/api/v1/workspace/knowledge/' + SOURCE + '?confirm=true').status_code == 204


@pytest.mark.parametrize('name,args', [
    ('create_knowledge_source', {'title': 'T', 'content': 'C', 'approved': 'true'}),
    ('create_knowledge_source', {'title': 'T', 'content': 'C', 'created_by': OWNER}),
    ('create_knowledge_source', {'title': 'T', 'content': 'C', 'enabled': 1}),
    ('update_knowledge_source', {'source_id': SOURCE, 'approved': 1}),
    ('update_knowledge_source', {'source_id': 'invalid', 'enabled': True}),
    ('list_knowledge_sources', {'limit': True}),
    ('list_knowledge_sources', {'limit': 101}),
    ('list_knowledge_sources', {'offset': -1}),
    ('delete_knowledge_source', {'source_id': SOURCE, 'confirm': 1}),
    ('rename_account', {'account_id': ACCOUNT, 'label': 'Valid', 'status': 'closed'}),
    ('draft_reply', {'query': 'Q', 'tool': 'send_message'}),
    ('draft_reply', {'query': 'Q', 'persona_id': 'not-a-uuid'}),
    ('create_persona', {'name': 'Sales', 'instructions': 'Be warm', 'send': True}),
    ('create_persona', {'name': 'Sales', 'instructions': 'Be warm', 'created_by': OWNER}),
    ('update_persona', {'persona_id': SOURCE, 'enabled': 1}),
    ('update_persona', {'persona_id': 'invalid', 'name': 'X'}),
    ('delete_persona', {'persona_id': SOURCE, 'confirm': 1}),
])
def test_strict_mcp_schema_rejects_before_storage(name, args, monkeypatch):
    grant(*workspace.KEY_SCOPES)
    storage = Mock()
    monkeypatch.setattr(api_workspace, 'rest', storage)
    assert call(name, args).json()['error']['code'] == -32602
    storage.assert_not_called()


def test_account_scope_only_updates_workspace_label(monkeypatch):
    grant('accounts:write')
    storage = Mock(return_value=[{'id': ACCOUNT, 'label': 'New'}])
    monkeypatch.setattr(api_workspace, 'rest', storage)
    assert call('rename_account', {'account_id': ACCOUNT, 'label': ' New '}).json()['result']['isError'] is False
    storage.assert_called_once_with('POST', 'rpc/open_tgate_mutate_workspace', body={
        'resource_name': 'account_label', 'operation_name': 'UPDATE', 'record_id': ACCOUNT,
        'payload': {'label': 'New'}, 'actor_user': OWNER, 'actor_key': None})
    storage.reset_mock()
    assert client.patch('/api/v1/workspace/accounts/' + ACCOUNT + '/label', json={'label': ' '}).status_code == 422
    assert client.patch('/api/v1/workspace/accounts/' + ACCOUNT + '/label', json={'label': 'T', 'status': 'closed'}).status_code == 422
    grant('read')
    assert call('rename_account', {'account_id': ACCOUNT, 'label': 'T'}).status_code == 403
    storage.assert_not_called()


@pytest.mark.parametrize('scopes,args,status', [
    (['ai:draft'], {'query': 'Q'}, 403),
    (['knowledge:read'], {'query': 'Q'}, 403),
    (['ai:draft', 'knowledge:read'], {'query': 'Q', 'account_id': ACCOUNT, 'chat_id': '42'}, 403),
    (['ai:draft', 'knowledge:read'], {'query': 'Q', 'account_id': ACCOUNT}, 422),
    (['ai:draft', 'knowledge:read'], {'query': ' '}, 422),
])
def test_draft_authorization_and_validation_precede_retrieval(scopes, args, status, monkeypatch):
    grant(*scopes)
    search = Mock()
    monkeypatch.setattr(api_workspace, 'knowledge_search', search)
    assert client.post('/api/v1/workspace/ai/draft', json=args).status_code == status
    response = call('draft_reply', args)
    if status == 403:
        assert response.status_code == 403
    else:
        assert response.json()['result']['isError'] is True
    search.assert_not_called()


def test_draft_without_history_requires_only_two_scopes(monkeypatch):
    grant('ai:draft', 'knowledge:read')
    monkeypatch.setattr(api_workspace, 'knowledge_search', lambda query: [])
    monkeypatch.setattr(api_workspace, 'get_settings', lambda: Settings(open_connect_mcp_key='', open_connect_ai_model=''))
    response = call('draft_reply', {'query': 'What is the policy?'})
    assert json.loads(response.json()['result']['content'][0]['text'])['configured'] is False


def test_keys_and_admin_token_do_not_become_operators(monkeypatch):
    settings = Settings(api_admin_token='test-admin-only')
    monkeypatch.setattr(workspace, 'get_settings', lambda: settings)
    admin = workspace.require_reader('Bearer test-admin-only')
    assert admin.scopes == workspace.DEFAULT_SCOPES and admin.operator is False
    rows = [{'id': SOURCE, 'created_by': OWNER, 'owner_email': 'operator@example.test', 'scopes': sorted(workspace.KEY_SCOPES)}]
    monkeypatch.setattr(workspace, 'rest', Mock(side_effect=[rows, True]))
    key = workspace.require_reader('Bearer otg_test-only')
    assert key.scopes == workspace.KEY_SCOPES and key.operator is False and key.key_id == SOURCE
    monkeypatch.setattr(workspace, 'get_settings', lambda: Settings(supabase_url='https://example.test', supabase_secret_key='test-key'))
    monkeypatch.setattr(workspace.httpx, 'get', Mock(return_value=httpx.Response(401, request=httpx.Request('GET', 'https://example.test'))))
    grant(*workspace.KEY_SCOPES)
    for path in ('/keys', '/keys/' + SOURCE + '/revoke'):
        assert client.post('/api/v1/workspace' + path, headers={'Authorization': 'Bearer otg_test-only'}, json={'name': 'No'}).status_code == 401


def test_key_and_oauth_defaults_remain_read_only(monkeypatch):
    assert set(api_workspace.KeyRequest(name='default').scopes) == workspace.DEFAULT_SCOPES
    body = oauth.Authorization(client_id=uuid.uuid4(), redirect_uri='https://chatgpt.com/callback',
        code_challenge='a' * 43, resource='https://open-tgate.site/mcp')
    assert set(body.scope.split()) == workspace.DEFAULT_SCOPES
    monkeypatch.setattr(oauth, 'get_settings', lambda: Settings(dashboard_origin='https://open-tgate.site'))
    monkeypatch.setattr(oauth, 'rest', lambda *args, **kwargs: [{'redirect_uris': [body.redirect_uri]}])
    assert set(oauth.protected_metadata()['scopes_supported']) == workspace.KEY_SCOPES
    body.scope = 'knowledge:write ai:draft accounts:write knowledge:read'
    assert oauth.validate_authorization(body)


PERSONA = str(uuid.uuid4())


@pytest.mark.parametrize('scopes', [[], ['read'], ['knowledge:write'], ['accounts:write']])
def test_persona_management_requires_ai_draft_scope(scopes, monkeypatch):
    grant(*scopes)
    storage = Mock()
    monkeypatch.setattr(api_workspace, 'rest', storage)
    assert client.get('/api/v1/workspace/personas').status_code == 403
    assert client.post('/api/v1/workspace/personas', json={'name': 'Sales', 'instructions': 'Be warm'}).status_code == 403
    assert client.patch('/api/v1/workspace/personas/' + PERSONA, json={'enabled': False}).status_code == 403
    assert client.delete('/api/v1/workspace/personas/' + PERSONA + '?confirm=true').status_code == 403
    assert call('create_persona', {'name': 'Sales', 'instructions': 'Be warm'}).status_code == 403
    storage.assert_not_called()


def test_persona_rest_and_mcp_share_validated_operations(monkeypatch):
    grant('ai:draft')
    storage = Mock(return_value=[{'id': PERSONA, 'name': 'Sales', 'instructions': 'Be warm', 'enabled': True}])
    monkeypatch.setattr(api_workspace, 'rest', storage)
    created = call('create_persona', {'name': 'Sales', 'instructions': 'Be warm'})
    assert created.json()['result']['isError'] is False
    body = storage.call_args.kwargs['body']
    assert body['resource_name'] == 'persona' and body['operation_name'] == 'INSERT'
    assert body['actor_user'] == OWNER and body['payload'] == {'name': 'Sales', 'instructions': 'Be warm', 'enabled': True}
    assert call('update_persona', {'persona_id': PERSONA, 'instructions': 'Be concise'}).json()['result']['isError'] is False
    assert storage.call_args.kwargs['body']['payload'] == {'instructions': 'Be concise'}
    storage.reset_mock()
    # Deletion requires explicit confirmation at both the REST and MCP surfaces.
    assert client.delete('/api/v1/workspace/personas/' + PERSONA).status_code == 422
    assert call('delete_persona', {'persona_id': PERSONA, 'confirm': False}).json()['error']['code'] == -32602
    storage.assert_not_called()
    assert call('delete_persona', {'persona_id': PERSONA, 'confirm': True}).json()['result']['isError'] is False
    assert storage.call_args.kwargs['body'] == {
        'resource_name': 'persona', 'operation_name': 'DELETE', 'record_id': PERSONA,
        'payload': {}, 'actor_user': OWNER, 'actor_key': None}


def test_draft_with_persona_shapes_voice_but_never_sends(monkeypatch):
    grant('ai:draft', 'knowledge:read')
    monkeypatch.setattr(api_workspace, 'get_settings',
                        lambda: Settings(open_connect_mcp_key='server-gateway-key', open_connect_ai_model='configured-model'))
    monkeypatch.setattr(api_workspace, 'knowledge_search',
                        lambda query: [{'id': 'source', 'title': 'Policy', 'excerpt': 'Support daily'}])
    monkeypatch.setattr(api_workspace, 'rest',
                        lambda method, path, **kwargs: [{'id': PERSONA, 'name': 'Sunny Sales',
                                                         'instructions': 'Warm, upbeat, concise.', 'enabled': True}])

    captured = {}

    class StubGateway:
        def __init__(self, url, key):
            self.configured = True

        def draft_completion(self, model, messages):
            captured['messages'] = messages
            return 'Happy to help! (Policy)'

    monkeypatch.setattr(api_workspace, 'OpenConnectMCP', StubGateway)
    response = call('draft_reply', {'query': 'Support hours?', 'persona_id': PERSONA})
    payload = json.loads(response.json()['result']['content'][0]['text'])
    assert payload['configured'] is True and payload['draft'] == 'Happy to help! (Policy)'
    assert payload['persona']['name'] == 'Sunny Sales'
    system = captured['messages'][0]['content']
    # Persona voice is injected, but the non-negotiable safety rules remain.
    assert 'Sunny Sales' in system and 'Warm, upbeat, concise.' in system
    assert 'Never send it' in system and 'never authorizes sending' in system


def test_draft_rejects_unknown_or_disabled_persona(monkeypatch):
    grant('ai:draft', 'knowledge:read')
    search = Mock()
    monkeypatch.setattr(api_workspace, 'knowledge_search', search)
    monkeypatch.setattr(api_workspace, 'rest', lambda method, path, **kwargs: [])  # persona not found
    assert client.post('/api/v1/workspace/ai/draft', json={'query': 'Q', 'persona_id': PERSONA}).status_code == 404
    monkeypatch.setattr(api_workspace, 'rest',
                        lambda method, path, **kwargs: [{'id': PERSONA, 'name': 'Off', 'instructions': 'x', 'enabled': False}])
    assert client.post('/api/v1/workspace/ai/draft', json={'query': 'Q', 'persona_id': PERSONA}).status_code == 404
    search.assert_not_called()  # persona is resolved before any knowledge retrieval


def test_mutation_rpc_attributes_verified_key_and_ignores_actor_injection(monkeypatch):
    principal = workspace.Principal(OWNER, scopes=frozenset({'accounts:write'}), key_id=SOURCE)
    app.dependency_overrides[workspace.require_reader] = lambda: principal
    storage = Mock(return_value=[{'id': ACCOUNT, 'label': 'T'}])
    monkeypatch.setattr(api_workspace, 'rest', storage)
    assert call('rename_account', {'account_id': ACCOUNT, 'label': 'T'}).json()['result']['isError'] is False
    body = storage.call_args.kwargs['body']
    assert body['actor_user'] == OWNER and body['actor_key'] == SOURCE
    storage.reset_mock()
    assert call('rename_account', {'account_id': ACCOUNT, 'label': 'T', 'actor_user': SOURCE}).json()['error']['code'] == -32602
    storage.assert_not_called()
