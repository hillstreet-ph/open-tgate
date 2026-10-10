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
from app.integrations import openconnect
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


def test_message_activity_filters_are_scoped_and_use_bound_rpc_arguments(monkeypatch):
    app.dependency_overrides[workspace.require_reader] = lambda: OPERATOR
    storage = Mock(return_value=[{'text': '<script>conversation</script>', 'message_id': '9223372036854775807'}])
    monkeypatch.setattr(api_workspace, 'rest', storage)
    account = str(uuid.uuid4())
    response = client.get('/api/v1/workspace/message-activity', params={
        'account_id': account, 'kind': 'bot', 'folder_id': 6, 'query': "quotes ' and %", 'limit': 20})
    assert response.status_code == 200
    assert response.json()['messages'][0]['message_id'] == '9223372036854775807'
    storage.assert_called_once_with('POST', 'rpc/open_tgate_message_activity', body={
        'account': account, 'chat_kind': 'bot', 'folder': 6, 'term': "quotes ' and %",
        'result_limit': 20, 'result_offset': 0})
    storage.reset_mock()
    for params in ({'folder_id': 6}, {'kind': 'invalid'}, {'limit': 201}, {'query': 'x' * 1001}):
        assert client.get('/api/v1/workspace/message-activity', params=params).status_code == 422
    storage.assert_not_called()


def test_activity_cursor_round_trip_preserves_large_ids_and_binds_boundary(monkeypatch):
    app.dependency_overrides[workspace.require_reader] = lambda: OPERATOR
    row = {'account_id': str(uuid.uuid4()), 'chat_id': '-100123',
           'message_id': '9223372036854775806', 'activity_at': '2026-01-03T00:00:00+00:00'}
    storage = Mock(return_value=[row])
    monkeypatch.setattr(api_workspace, 'rest', storage)
    first = client.get('/api/v1/workspace/message-activity', params={'limit': 1}).json()
    cursor = first['next_cursor']
    assert cursor
    assert client.get('/api/v1/workspace/message-activity', params={'limit': 1, 'cursor': cursor}).status_code == 200
    body = storage.call_args.kwargs['body']
    assert body['before_message'] == 9223372036854775806
    assert body['before_account'] == row['account_id']
    assert body['before_chat'] == '-100123'
    assert body['before_at'] == row['activity_at']
    storage.reset_mock()
    assert client.get('/api/v1/workspace/message-activity', params={'cursor': cursor, 'offset': 1}).status_code == 422
    for invalid in ('{}', 'e30', 'W10', 'x'*601):
        assert client.get('/api/v1/workspace/message-activity', params={'cursor': invalid}).status_code == 422
    storage.assert_not_called()


def test_activity_cursor_supports_missing_timestamp_and_rejects_malformed_fields():
    row = {'activity_at': None, 'message_id': '1', 'account_id': str(uuid.uuid4()), 'chat_id': '12'}
    assert api_workspace.activity_boundary(api_workspace.activity_page([row], 1)['next_cursor'])['before_at'] is None
    import base64
    import json
    for values in ([True,'1',row['account_id'],'12'], ['2026-01-01','1',row['account_id'],'12'],
                   [None,True,row['account_id'],'12'], [None,'1','invalid','12'], [None,'1',row['account_id'],'invalid']):
        cursor = base64.urlsafe_b64encode(json.dumps(values).encode()).decode().rstrip('=')
        with pytest.raises(HTTPException):
            api_workspace.activity_boundary(cursor)


@pytest.mark.parametrize('arguments', [
    {'folder_id': 6}, {'folder_id': True}, {'kind': {}}, {'kind': 'private'},
    {'query': []}, {'query': 'x' * 1001}, {'limit': True}, {'offset': -1}])
def test_conversation_search_rejects_invalid_mcp_arguments_before_storage(monkeypatch, arguments):
    app.dependency_overrides[workspace.require_reader] = lambda: OPERATOR
    storage = Mock()
    monkeypatch.setattr(api_workspace, 'rest', storage)
    response = client.post('/mcp', json={'jsonrpc': '2.0', 'id': 1, 'method': 'tools/call',
        'params': {'name': 'search_messages', 'arguments': arguments}})
    assert response.json()['error']['code'] == -32602
    storage.assert_not_called()


def test_conversation_search_is_read_only_and_requires_read_scope(monkeypatch):
    app.dependency_overrides[workspace.require_reader] = lambda: workspace.Principal(USER_ID, 'operator@example.test', scopes=['knowledge:read'])
    storage = Mock()
    monkeypatch.setattr(api_workspace, 'rest', storage)
    request = {'jsonrpc': '2.0', 'id': 1, 'method': 'tools/call',
               'params': {'name': 'search_messages', 'arguments': {'query': 'conversation'}}}
    assert client.post('/mcp', json=request).status_code == 403
    assert client.get('/api/v1/workspace/message-activity').status_code == 403
    storage.assert_not_called()


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
    storage = Mock(side_effect=[rows, False])
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
    assert client.get('/api/v1/workspace/keys').json() == {'keys': [], 'has_more': False, 'next_offset': None}
    assert client.post('/api/v1/workspace/keys', json={'name': 'unsafe', 'scopes': ['send']}).status_code == 422


def test_key_revocation_is_bound_to_creator(monkeypatch):
    operator()
    storage = Mock(return_value=[])
    monkeypatch.setattr(api_workspace, 'rest', storage)
    assert client.post(f'/api/v1/workspace/keys/{uuid.uuid4()}/revoke').status_code == 404
    assert storage.call_args.kwargs['params']['created_by'] == f'eq.{USER_ID}'


def test_retrieval_uses_bounded_full_corpus_database_search(monkeypatch):
    storage = Mock(return_value=[{'id': 'approved', 'title': 'Policy', 'excerpt': 'Bounded facts'}])
    monkeypatch.setattr(workspace, 'rest', storage)
    assert workspace.knowledge_search('refund')[0]['id'] == 'approved'
    storage.assert_called_once_with('POST', 'rpc/open_tgate_search_knowledge',
                                    body={'query_text': 'refund', 'result_limit': 5})


def test_ai_unconfigured_does_not_fake_a_draft(monkeypatch):
    operator()
    monkeypatch.setattr(api_workspace, 'get_settings', lambda: Settings(open_connect_mcp_key='', open_connect_ai_model=''))
    monkeypatch.setattr(api_workspace, 'knowledge_search', lambda query: [])
    response = client.post('/api/v1/workspace/ai/draft', json={'query': 'What is our policy?'})
    assert response.status_code == 200
    assert response.json()['configured'] is False
    assert response.json()['draft'] is None


def test_ai_grounding_and_draft_only_provider_request(monkeypatch):
    operator()
    monkeypatch.setattr(api_workspace, 'get_settings', lambda: Settings(open_connect_mcp_key='server-gateway-key', open_connect_ai_model='configured-model'))
    monkeypatch.setattr(api_workspace, 'knowledge_search', lambda query: [{'id': 'source', 'title': 'Policy', 'excerpt': 'Support daily'}])
    provider = Mock(return_value=httpx.Response(200, request=httpx.Request('POST', 'https://example.test'),
                    json={'choices': [{'message': {'content': 'Support is available daily (Policy).'}}]}))
    monkeypatch.setattr(openconnect.httpx, 'post', provider)
    response = client.post('/api/v1/workspace/ai/draft', json={'query': 'Support?'})
    assert response.status_code == 200
    assert response.json()['sources'][0]['id'] == 'source'
    request = provider.call_args.kwargs['json']
    assert 'Never send it' in request['messages'][0]['content']
    assert 'approved_sources' in request['messages'][1]['content']
    assert 'server-gateway-key' not in response.text
    assert provider.call_args.args[0] == 'https://open-connect.site/v1/chat/completions'
    assert provider.call_args.kwargs['headers']['Authorization'] == 'Bearer server-gateway-key'
    assert request['stream'] is False and 'tools' not in request


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


@pytest.mark.parametrize('resource', ['keys', 'knowledge'])
def test_management_pages_keep_older_items_accessible(resource, monkeypatch):
    operator()
    item_name = 'keys' if resource == 'keys' else 'sources'
    rows = [{'id': str(index), 'revoked_at': None} for index in range(205)]
    calls = []
    def storage(method, table, **kwargs):
        if resource == 'knowledge':
            assert method == 'POST' and table == 'rpc/open_tgate_list_knowledge_previews'
            assert 'params' not in kwargs
            params = {'limit': kwargs['body']['page_limit'], 'offset': kwargs['body']['page_offset']}
        else:
            params = kwargs['params']
            assert params['order'] == 'created_at.desc,id.asc'
        calls.append(params)
        assert params['limit'] <= 101
        return rows[params['offset']:params['offset'] + params['limit']]
    monkeypatch.setattr(api_workspace, 'rest', storage)
    seen = []
    offset = 0
    while True:
        page = client.get(f'/api/v1/workspace/{resource}?limit=100&offset={offset}').json()
        seen.extend(row['id'] for row in page[item_name])
        if not page['has_more']:
            assert page['next_offset'] is None
            break
        offset = page['next_offset']
    assert seen == [str(index) for index in range(205)]
    assert len(calls) == 3
    if resource == 'keys':
        assert all(call['created_by'] == f'eq.{USER_ID}' for call in calls)


def test_cross_account_pagination_uses_unique_total_order(monkeypatch):
    app.dependency_overrides[workspace.require_reader] = lambda: OPERATOR
    storage = Mock(return_value=[])
    monkeypatch.setattr(api_workspace, 'rest', storage)
    assert client.get('/api/v1/workspace/chats').status_code == 200
    assert storage.call_args.kwargs['params']['order'] == 'last_message_at.desc.nullslast,chat_id.asc,account_id.asc'
    assert storage.call_args.kwargs['params']['is_visible'] == 'eq.true'
    selected = storage.call_args.kwargs['params']['select'].split(',')
    assert 'recent_complete' in selected and 'recent_note' in selected
    assert client.get('/api/v1/workspace/contacts').status_code == 200
    assert storage.call_args.kwargs['params']['order'] == 'title.asc.nullslast,tg_id.asc,account_id.asc'


def test_mcp_history_cursor_roundtrips_exact_bigint_string(monkeypatch):
    app.dependency_overrides[workspace.require_reader] = lambda: OPERATOR
    storage = Mock(return_value=[])
    monkeypatch.setattr(api_workspace, 'rest', storage)
    request = {'jsonrpc': '2.0', 'id': 1, 'method': 'tools/call', 'params': {
        'name': 'get_history', 'arguments': {'account_id': str(uuid.uuid4()), 'chat_id': '123',
                                          'before': '9223372036854775807'}}}
    response = client.post('/mcp', json=request)
    assert response.status_code == 200 and not response.json()['result']['isError']
    assert storage.call_args.kwargs['params']['message_id'] == 'lt.9223372036854775807'
    assert client.get('/api/v1/workspace/messages', params={'account_id': str(uuid.uuid4()),
        'chat_id': '123', 'before': '9223372036854775807'}).status_code == 200
    assert storage.call_args.kwargs['params']['message_id'] == 'lt.9223372036854775807'


@pytest.mark.parametrize('before', [True, False, 1.5, '', '1.5', '123&deleted=eq.true',
                                   '9223372036854775808', '-9223372036854775809', [], {}])
def test_mcp_history_rejects_invalid_cursors_without_storage(before, monkeypatch):
    app.dependency_overrides[workspace.require_reader] = lambda: OPERATOR
    storage = Mock()
    monkeypatch.setattr(api_workspace, 'rest', storage)
    request = {'jsonrpc': '2.0', 'id': 1, 'method': 'tools/call', 'params': {
        'name': 'get_history', 'arguments': {'account_id': str(uuid.uuid4()), 'chat_id': '123', 'before': before}}}
    response = client.post('/mcp', json=request)
    assert response.json()['error']['code'] == -32602
    storage.assert_not_called()


def test_gateway_generation_errors_are_safe_and_cannot_change_endpoint(monkeypatch):
    provider = Mock(return_value=httpx.Response(200, request=httpx.Request('POST', 'https://example.test'),
        json={'error': {'message': 'provider detail server-gateway-key private facts'}}))
    monkeypatch.setattr(openconnect.httpx, 'post', provider)
    gateway = openconnect.OpenConnectMCP('https://untrusted.test/mcp', 'server-gateway-key')
    with pytest.raises(openconnect.OpenConnectError) as exc:
        gateway.draft_completion('configured-model', [{'role': 'user', 'content': 'private facts'}])
    assert str(exc.value) == 'open_connect_model_error'
    assert provider.call_args.args[0] == 'https://open-connect.site/v1/chat/completions'
    assert provider.call_args.kwargs['follow_redirects'] is False


def test_gateway_ai_cannot_invoke_tools_or_provider_urls_from_request(monkeypatch):
    operator()
    monkeypatch.setattr(api_workspace, 'get_settings', lambda: Settings(
        open_connect_mcp_key='server-gateway-key', open_connect_ai_model='configured-model'))
    monkeypatch.setattr(api_workspace, 'knowledge_search', lambda query: [{'id': 'approved', 'title': 'Facts', 'excerpt': 'Approved facts'}])
    provider = Mock(return_value=httpx.Response(200, request=httpx.Request('POST', 'https://example.test'),
        json={'choices': [{'message': {'content': 'Review-only draft'}}]}))
    monkeypatch.setattr(openconnect.httpx, 'post', provider)
    response = client.post('/api/v1/workspace/ai/draft', json={'query': 'Support?',
        'tool': 'send_message', 'ai_base_url': 'https://untrusted.test', 'model': 'client-selected'})
    assert response.status_code == 200
    payload = provider.call_args.kwargs['json']
    assert payload['model'] == 'configured-model'
    assert 'tools' not in payload and 'tool' not in payload
    assert provider.call_args.args[0] == 'https://open-connect.site/v1/chat/completions'


@pytest.mark.parametrize('result', [None, [], 'invalid', {'choices': []}])
def test_gateway_malformed_generation_response_is_sanitized(result, monkeypatch):
    provider = Mock(return_value=httpx.Response(200, request=httpx.Request('POST', 'https://example.test'), json=result))
    monkeypatch.setattr(openconnect.httpx, 'post', provider)
    gateway = openconnect.OpenConnectMCP('https://open-connect.site/mcp', 'server-gateway-key')
    with pytest.raises(openconnect.OpenConnectError) as exc:
        gateway.draft_completion('configured-model', [{'role': 'user', 'content': 'facts'}])
    assert 'server-gateway-key' not in str(exc.value)
    assert str(exc.value) in {'open_connect_model_error', 'open_connect_model_unavailable'}


def test_scoped_key_owner_activation_queries_one_owner_only(monkeypatch):
    monkeypatch.setattr(workspace, 'get_settings', lambda: Settings(api_admin_token='admin-secret'))
    rows = [{'created_by': USER_ID, 'owner_email': 'Operator@Example.test', 'scopes': ['read']}]
    storage = Mock(side_effect=[rows, True])
    monkeypatch.setattr(workspace, 'rest', storage)
    principal = workspace.require_reader('Bearer otg_secret')
    assert principal.user_id == USER_ID and principal.scopes == frozenset({'read'})
    assert storage.call_args_list[1].args == ('POST', 'rpc/open_tgate_api_key_owner_active')
    assert storage.call_args_list[1].kwargs == {'body': {'owner_email': 'Operator@Example.test'}}


def test_knowledge_list_uses_database_previews_before_transport(monkeypatch):
    operator()
    previews = [{'id': str(index), 'title': 'Unicode policy', 'content': '界' * 180,
                 'source_type': 'text', 'approved': True, 'enabled': True}
                for index in range(101)]
    storage = Mock(return_value=previews)
    monkeypatch.setattr(api_workspace, 'rest', storage)
    response = client.get('/api/v1/workspace/knowledge?limit=100&offset=100')
    assert response.status_code == 200
    page = response.json()
    assert len(page['sources']) == 100 and page['has_more'] and page['next_offset'] == 200
    assert all(len(source['content']) == 180 for source in page['sources'])
    assert len(response.text) < 50000
    storage.assert_called_once_with('POST', 'rpc/open_tgate_list_knowledge_previews',
                                    body={'page_limit': 101, 'page_offset': 100})


def test_knowledge_preview_list_requires_operator_before_storage(monkeypatch):
    storage = Mock()
    monkeypatch.setattr(api_workspace, 'rest', storage)
    assert client.get('/api/v1/workspace/knowledge').status_code == 401
    storage.assert_not_called()


@pytest.mark.parametrize('query', ['limit=0', 'limit=101', 'offset=-1', 'offset=2147483648'])
def test_knowledge_preview_list_validates_page_bounds(query, monkeypatch):
    operator()
    storage = Mock()
    monkeypatch.setattr(api_workspace, 'rest', storage)
    assert client.get('/api/v1/workspace/knowledge?' + query).status_code == 422
    storage.assert_not_called()


@pytest.mark.parametrize('auth_status, expected_status, detail', [
    (401, 401, 'unauthorized'), (403, 401, 'unauthorized'),
    (429, 503, 'identity_service_unavailable'), (500, 503, 'identity_service_unavailable'),
    (502, 503, 'identity_service_unavailable'), (503, 503, 'identity_service_unavailable'),
    (504, 503, 'identity_service_unavailable'),
])
def test_auth_rate_limits_and_outages_do_not_invalidate_valid_sessions(auth_status, expected_status, detail, monkeypatch):
    monkeypatch.setattr(workspace, 'get_settings', lambda: Settings(
        supabase_url='https://example.test', supabase_secret_key='server-only'))
    monkeypatch.setattr(workspace.httpx, 'get', lambda *args, **kwargs: httpx.Response(auth_status,
        request=httpx.Request('GET', 'https://example.test'),
        json={'message': 'upstream-private-detail-must-not-be-exposed'}))
    storage = Mock()
    monkeypatch.setattr(workspace, 'rest', storage)
    response = client.get('/api/v1/workspace/knowledge', headers={'Authorization': 'Bearer signed-user-jwt'})
    assert response.status_code == expected_status
    assert response.json() == {'detail': detail}
    assert 'upstream-private-detail' not in response.text
    storage.assert_not_called()


def test_mirrored_folders_and_folder_chats_require_read_scope_and_account(monkeypatch):
    app.dependency_overrides[workspace.require_reader] = lambda: OPERATOR
    storage = Mock(return_value=[{'chat_folders': [{'id': 7, 'title': 'Work'}], 'main_chat_list_position': 1}])
    monkeypatch.setattr(api_workspace, 'rest', storage)
    account = str(uuid.uuid4())
    response = client.get('/api/v1/workspace/folders', params={'account_id': account})
    assert response.json()['folders'][0]['id'] == 7
    assert storage.call_args.kwargs['params']['id'] == 'eq.' + account
    storage.return_value = []
    assert client.get('/api/v1/workspace/chats', params={'folder_id': 7}).status_code == 422
    response = client.get('/api/v1/workspace/chats', params={'account_id': account, 'folder_id': 7})
    assert response.status_code == 200
    assert storage.call_args.kwargs['params']['folder_ids'] == 'cs.{7}'
    assert storage.call_args.kwargs['params']['order'].startswith('folder_positions->>7.desc')
    for folder in [True, 0, -1, 2147483648, '7.desc']:
        response = client.post('/mcp', json={'jsonrpc': '2.0', 'id': 1, 'method': 'tools/call',
            'params': {'name': 'list_chats', 'arguments': {'account_id': account, 'folder_id': folder}}})
        assert response.json()['error']['code'] == -32602
    response = client.post('/mcp', json={'jsonrpc': '2.0', 'id': 1, 'method': 'tools/call',
        'params': {'name': 'list_folders', 'arguments': {'account_id': account}}})
    assert response.status_code == 200
    app.dependency_overrides[workspace.require_reader] = lambda: workspace.Principal(USER_ID, scopes=frozenset({'knowledge:read'}))
    assert client.get('/api/v1/workspace/folders', params={'account_id': account}).status_code == 403
