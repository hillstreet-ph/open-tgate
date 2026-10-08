"""Authorization, scoped integration keys and approved grounding regressions."""
import hashlib
import uuid
from unittest.mock import Mock

import httpx
import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient

from app import api_workspace, workspace
from app.config import Settings
from app.main import app

client = TestClient(app)
USER_ID = str(uuid.uuid4())
OPERATOR = workspace.Principal(USER_ID, 'operator@example.test', operator=True)


@pytest.fixture(autouse=True)
def clean_overrides():
    app.dependency_overrides.clear()
    yield
    app.dependency_overrides.clear()


def operator():
    app.dependency_overrides[workspace.require_operator] = lambda: OPERATOR


def test_workspace_and_mcp_require_bearer():
    assert client.get('/api/v1/workspace/accounts').status_code == 401
    assert client.post('/mcp', json={'jsonrpc': '2.0', 'id': 1, 'method': 'tools/list'}).status_code == 401
    assert client.post('/api/v1/workspace/knowledge', json={'title': 'Test', 'content': 'Test'}).status_code == 401


def test_operator_checks_current_allowlist_not_metadata(monkeypatch):
    monkeypatch.setattr(workspace, 'get_settings', lambda: Settings(supabase_url='https://example.test', supabase_secret_key='server-only'))
    monkeypatch.setattr(workspace.httpx, 'get', lambda *args, **kwargs: httpx.Response(200,
        request=httpx.Request('GET', 'https://example.test'), json={
        'id': USER_ID, 'email': 'operator@example.test', 'user_metadata': {'is_operator': True}}))
    checked = Mock(return_value=False)
    monkeypatch.setattr(workspace, 'rest', checked)
    with pytest.raises(HTTPException) as exc:
        workspace.require_operator('Bearer signed-user-jwt')
    assert exc.value.status_code == 403
    checked.assert_called_once_with('POST', 'rpc/open_tgate_is_operator', body={}, token='signed-user-jwt')


def test_key_hash_and_immediate_owner_deactivation(monkeypatch):
    monkeypatch.setattr(workspace, 'get_settings', lambda: Settings(api_admin_token='admin-secret'))
    rows = [{'created_by': USER_ID, 'owner_email': 'operator@example.test', 'scopes': ['read']}]
    storage = Mock(side_effect=[rows, []])
    monkeypatch.setattr(workspace, 'rest', storage)
    with pytest.raises(HTTPException) as exc:
        workspace.require_reader('Bearer otg_secret')
    assert exc.value.status_code == 403
    assert storage.call_args_list[0].kwargs['params']['token_hash'] == 'eq.' + hashlib.sha256(b'otg_secret').hexdigest()
    assert storage.call_args_list[0].kwargs['params']['revoked_at'] == 'is.null'


def test_key_create_returns_secret_once_and_list_never_hash(monkeypatch):
    operator()
    inserted = []
    def storage(method, table, **kwargs):
        if method == 'POST':
            inserted.append(kwargs['body'])
            return [{k: v for k, v in kwargs['body'].items() if k in api_workspace.KEY_FIELDS.split(',')}]
        assert 'token_hash' not in kwargs['params']['select']
        return []
    monkeypatch.setattr(api_workspace, 'rest', storage)
    response = client.post('/api/v1/workspace/keys', json={'name': 'Read-only agent', 'scopes': ['read']})
    assert response.status_code == 201
    token = response.json()['key']
    assert token.startswith('otg_')
    assert inserted[0]['token_hash'] == workspace.key_hash(token)
    assert token not in str(inserted)
    assert 'token_hash' not in response.json()
    assert client.get('/api/v1/workspace/keys').json() == {'keys': []}
    assert client.post('/api/v1/workspace/keys', json={'name': 'unsafe', 'scopes': ['send']}).status_code == 422


def test_key_revocation_is_bound_to_creator(monkeypatch):
    operator()
    storage = Mock(return_value=[])
    monkeypatch.setattr(api_workspace, 'rest', storage)
    assert client.post(f'/api/v1/workspace/keys/{uuid.uuid4()}/revoke').status_code == 404
    assert storage.call_args.kwargs['params']['created_by'] == f'eq.{USER_ID}'


def test_retrieval_excludes_unapproved_and_disabled_sources():
    rows = [{'id': 'approved', 'title': 'Refund policy', 'content': 'Refunds take 5 days.', 'approved': True, 'enabled': True},
            {'id': 'disabled', 'title': 'Refund', 'content': 'Bad facts', 'approved': True, 'enabled': False},
            {'id': 'unapproved', 'title': 'Refund', 'content': 'Bad facts', 'approved': False, 'enabled': True}]
    assert [row['id'] for row in workspace.retrieve_sources('refund', rows)] == ['approved']
    assert workspace.retrieve_sources('unrelated', rows) == []


def test_ai_unconfigured_does_not_fake_a_draft(monkeypatch):
    operator()
    monkeypatch.setattr(api_workspace, 'get_settings', lambda: Settings(ai_api_key='', ai_model=''))
    monkeypatch.setattr(api_workspace, 'knowledge_search', lambda query: [])
    response = client.post('/api/v1/workspace/ai/draft', json={'query': 'What is our policy?'})
    assert response.status_code == 200
    assert response.json()['configured'] is False
    assert response.json()['draft'] is None


def test_ai_grounding_and_draft_only_provider_request(monkeypatch):
    operator()
    monkeypatch.setattr(api_workspace, 'get_settings', lambda: Settings(ai_api_key='server-model-key', ai_model='configured-model'))
    monkeypatch.setattr(api_workspace, 'knowledge_search', lambda query: [{'id': 'source', 'title': 'Policy', 'excerpt': 'Support daily'}])
    provider = Mock(return_value=httpx.Response(200, request=httpx.Request('POST', 'https://example.test'),
                    json={'choices': [{'message': {'content': 'Support is available daily (Policy).'}}]}))
    monkeypatch.setattr(api_workspace.httpx, 'post', provider)
    response = client.post('/api/v1/workspace/ai/draft', json={'query': 'Support?'})
    assert response.status_code == 200
    assert response.json()['sources'][0]['id'] == 'source'
    request = provider.call_args.kwargs['json']
    assert 'Never send it' in request['messages'][0]['content']
    assert 'approved_sources' in request['messages'][1]['content']
    assert 'server-model-key' not in response.text


def test_mcp_read_scope_cannot_read_knowledge_or_write(monkeypatch):
    app.dependency_overrides[workspace.require_reader] = lambda: workspace.Principal(USER_ID, scopes=frozenset({'read'}))
    request = {'jsonrpc': '2.0', 'id': 1, 'method': 'tools/list'}
    names = [row['name'] for row in client.post('/mcp', json=request).json()['result']['tools']]
    assert 'get_history' in names and 'search_knowledge' not in names
    assert not any('send' in name or 'write' in name for name in names)
    request.update(method='tools/call', params={'name': 'search_knowledge', 'arguments': {'query': 'policy'}})
    assert client.post('/mcp', json=request).status_code == 403
    request['params'] = {'name': 'send_message', 'arguments': {}}
    assert client.post('/mcp', json=request).json()['error']['code'] == -32601


def test_mcp_validates_arguments_and_excludes_deleted_messages(monkeypatch):
    app.dependency_overrides[workspace.require_reader] = lambda: OPERATOR
    storage = Mock(return_value=[])
    monkeypatch.setattr(api_workspace, 'rest', storage)
    request = {'jsonrpc': '2.0', 'id': 1, 'method': 'tools/call', 'params': {
        'name': 'get_history', 'arguments': {'account_id': str(uuid.uuid4()), 'chat_id': '123', 'limit': 5}}}
    response = client.post('/mcp', json=request)
    assert response.status_code == 200
    assert storage.call_args.kwargs['params']['deleted'] == 'eq.false'
    request['params']['arguments']['chat_id'] = '123&deleted=eq.true'
    assert client.post('/mcp', json=request).json()['error']['code'] == -32602
    request['params']['arguments']['chat_id'] = '123'
    request['params']['arguments']['limit'] = True
    assert client.post('/mcp', json=request).json()['error']['code'] == -32602


def test_mcp_notifications_and_initialize():
    app.dependency_overrides[workspace.require_reader] = lambda: OPERATOR
    assert client.post('/mcp', json={'jsonrpc': '2.0', 'method': 'notifications/initialized'}).status_code == 202
    response = client.post('/mcp', json={'jsonrpc': '2.0', 'id': 1, 'method': 'initialize'})
    assert response.json()['result']['capabilities'] == {'tools': {}}


@pytest.mark.parametrize('account_id', [123, True, [], {}, '', None])
def test_mcp_rejects_malformed_account_ids_without_querying_storage(account_id, monkeypatch):
    app.dependency_overrides[workspace.require_reader] = lambda: OPERATOR
    storage = Mock()
    monkeypatch.setattr(api_workspace, 'rest', storage)
    request = {'jsonrpc': '2.0', 'id': 1, 'method': 'tools/call', 'params': {
        'name': 'list_chats', 'arguments': {'account_id': account_id}}}
    response = client.post('/mcp', json=request)
    assert response.status_code == 200
    assert response.json()['error']['code'] == -32602
    storage.assert_not_called()


def test_mcp_rejects_multiple_minus_in_chat_id(monkeypatch):
    app.dependency_overrides[workspace.require_reader] = lambda: OPERATOR
    storage = Mock()
    monkeypatch.setattr(api_workspace, 'rest', storage)
    response = client.post('/mcp', json={'jsonrpc': '2.0', 'id': 1, 'method': 'tools/call',
        'params': {'name': 'get_history', 'arguments': {'account_id': str(uuid.uuid4()), 'chat_id': '--123'}}})
    assert response.json()['error']['code'] == -32602
    storage.assert_not_called()
