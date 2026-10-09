"""Operator-consented OAuth 2.1 for the existing read-only MCP resource."""
from __future__ import annotations

import base64
import hashlib
import re
import secrets
import uuid
from urllib.parse import parse_qs, urlencode, urlsplit

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import JSONResponse, RedirectResponse
from pydantic import BaseModel, Field, ValidationError

from .config import get_settings
from .workspace import KEY_SCOPES, Principal, key_hash, require_operator, rest

router = APIRouter()
NO_STORE = {'Cache-Control': 'no-store', 'Pragma': 'no-cache'}


def origin() -> str:
    return get_settings().dashboard_origin.rstrip('/')


def resource() -> str:
    return origin() + '/mcp'


def oauth_error(message: str, status: int = 400) -> HTTPException:
    return HTTPException(status, message, headers=NO_STORE)


@router.get('/.well-known/oauth-protected-resource')
@router.get('/.well-known/oauth-protected-resource/mcp')
def protected_metadata():
    return {'resource': resource(), 'authorization_servers': [origin()],
            'scopes_supported': sorted(KEY_SCOPES), 'bearer_methods_supported': ['header']}


@router.get('/.well-known/oauth-authorization-server')
def authorization_metadata():
    return {'issuer': origin(), 'authorization_endpoint': origin() + '/oauth/authorize',
            'token_endpoint': origin() + '/oauth/token', 'registration_endpoint': origin() + '/oauth/register',
            'revocation_endpoint': origin() + '/oauth/revoke',
            'response_types_supported': ['code'], 'grant_types_supported': ['authorization_code', 'refresh_token'],
            'token_endpoint_auth_methods_supported': ['none'], 'code_challenge_methods_supported': ['S256'],
            'scopes_supported': sorted(KEY_SCOPES)}


class Registration(BaseModel):
    client_name: str = Field(default='MCP client', min_length=1, max_length=80)
    redirect_uris: list[str] = Field(min_length=1, max_length=5)
    token_endpoint_auth_method: str = 'none'
    grant_types: list[str] = Field(default_factory=lambda: ['authorization_code', 'refresh_token'])
    response_types: list[str] = Field(default_factory=lambda: ['code'])


@router.post('/oauth/register', status_code=201)
def register(body: Registration):
    if body.token_endpoint_auth_method != 'none' or set(body.grant_types) - {'authorization_code', 'refresh_token'} or body.response_types != ['code']:
        raise oauth_error('invalid_client_metadata')
    allowed = set(get_settings().oauth_redirect_hosts.split(','))
    for value in body.redirect_uris:
        try:
            parsed = urlsplit(value)
            port = parsed.port
        except ValueError as exc:
            raise oauth_error('invalid_redirect_uri') from exc
        if (len(value) > 2048 or parsed.scheme != 'https' or parsed.hostname not in allowed
                or parsed.username or parsed.password or parsed.fragment or port not in (None, 443)):
            raise oauth_error('invalid_redirect_uri')
    client_id = str(uuid.uuid4())
    # RPC prunes abandoned registrations and caps total clients under a lock.
    rest('POST', 'rpc/open_tgate_register_oauth_client', body={'client': client_id,
         'client_label': body.client_name, 'redirects': body.redirect_uris})
    return JSONResponse({'client_id': client_id, **body.model_dump()}, status_code=201, headers=NO_STORE)


class Authorization(BaseModel):
    client_id: uuid.UUID
    redirect_uri: str = Field(max_length=2048)
    response_type: str = 'code'
    code_challenge: str = Field(pattern=r'^[A-Za-z0-9_-]{43}$')
    code_challenge_method: str = 'S256'
    resource: str = Field(max_length=2048)
    scope: str = Field(default='read knowledge:read', max_length=100)
    state: str = Field(default='', max_length=2048)


def validate_authorization(body: Authorization) -> dict:
    if body.response_type != 'code' or body.code_challenge_method != 'S256':
        raise oauth_error('unsupported_response_type')
    if body.resource != resource():
        raise oauth_error('invalid_target')
    scopes = set(body.scope.split())
    if not scopes or not scopes <= KEY_SCOPES:
        raise oauth_error('invalid_scope')
    rows = rest('GET', 'open_tgate_oauth_clients', params={'client_id': f'eq.{body.client_id}', 'limit': 1})
    if not rows or body.redirect_uri not in rows[0]['redirect_uris']:
        raise oauth_error('invalid_client_or_redirect_uri')
    return rows[0]


@router.get('/oauth/authorize')
def authorize(request: Request):
    values = dict(request.query_params)
    preview = values.pop('preview', '')
    try:
        body = Authorization.model_validate(values)
    except ValidationError as exc:
        raise oauth_error('invalid_request') from exc
    client = validate_authorization(body)
    if preview == '1':
        return JSONResponse({'client_name': client['client_name'], 'redirect_uri': body.redirect_uri,
                             'scopes': sorted(set(body.scope.split()))}, headers=NO_STORE)
    context = urlencode(body.model_dump(mode='json'))
    return RedirectResponse(origin() + '/app?' + urlencode({'oauth_request': context}), headers=NO_STORE)


@router.post('/oauth/approve')
def approve(body: Authorization, principal: Principal = Depends(require_operator)):
    client = validate_authorization(body)
    code = secrets.token_urlsafe(32)
    rest('POST', 'open_tgate_oauth_codes', body={'code_hash': key_hash(code),
         'client_id': str(body.client_id), 'redirect_uri': body.redirect_uri,
         'challenge': body.code_challenge, 'resource': body.resource,
         'scopes': sorted(set(body.scope.split())), 'created_by': principal.user_id,
         'owner_email': principal.email, 'client_name': client['client_name']})
    parsed = urlsplit(body.redirect_uri)
    query = parsed.query + ('&' if parsed.query else '') + urlencode({'code': code, 'state': body.state})
    return JSONResponse({'redirect_uri': parsed._replace(query=query).geturl()}, headers=NO_STORE)


@router.post('/oauth/deny')
def deny(body: Authorization, principal: Principal = Depends(require_operator)):
    validate_authorization(body)
    parsed = urlsplit(body.redirect_uri)
    query = parsed.query + ('&' if parsed.query else '') + urlencode({'error': 'access_denied', 'state': body.state})
    return JSONResponse({'redirect_uri': parsed._replace(query=query).geturl()}, headers=NO_STORE)


async def form_data(request: Request) -> dict:
    if request.headers.get('content-type', '').split(';')[0].strip() != 'application/x-www-form-urlencoded':
        raise oauth_error('form_required', 415)
    chunks = bytearray()
    async for chunk in request.stream():
        chunks.extend(chunk)
        if len(chunks) > 16384:
            raise oauth_error('request_too_large', 413)
    try:
        values = parse_qs(chunks.decode('utf-8'), keep_blank_values=True, max_num_fields=20)
    except (ValueError, UnicodeError) as exc:
        raise oauth_error('invalid_request') from exc
    if any(len(v) != 1 for v in values.values()):
        raise oauth_error('invalid_request')
    return {k: v[0] for k, v in values.items()}


@router.post('/oauth/token')
async def token(request: Request):
    values = await form_data(request)
    if values.get('resource') != resource():
        raise oauth_error('invalid_target')
    try:
        client_id = str(uuid.UUID(values.get('client_id', '')))
    except ValueError as exc:
        raise oauth_error('invalid_client') from exc
    access = 'otg_' + secrets.token_urlsafe(32)
    refresh = secrets.token_urlsafe(48)
    args = {'client': client_id, 'target': resource(), 'access_hash': key_hash(access),
            'access_prefix': access[:12], 'refresh_hash': key_hash(refresh)}
    if values.get('grant_type') == 'authorization_code':
        verifier = values.get('code_verifier', '')
        if not re.fullmatch(r'[A-Za-z0-9._~-]{43,128}', verifier):
            raise oauth_error('invalid_grant')
        challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).rstrip(b'=').decode()
        args.update({'code_digest': key_hash(values.get('code', '')), 'pkce': challenge,
                     'redirect': values.get('redirect_uri', '')})
        result = rest('POST', 'rpc/open_tgate_exchange_oauth_code', body=args)
    elif values.get('grant_type') == 'refresh_token':
        args['previous_hash'] = key_hash(values.get('refresh_token', ''))
        result = rest('POST', 'rpc/open_tgate_refresh_oauth_token', body=args)
    else:
        raise oauth_error('unsupported_grant_type')
    if not result:
        raise oauth_error('invalid_grant')
    return JSONResponse({'access_token': access, 'token_type': 'Bearer', 'expires_in': 3600,
                         'refresh_token': refresh, 'scope': ' '.join(result['scopes'])}, headers=NO_STORE)


@router.post('/oauth/revoke')
async def revoke(request: Request):
    values = await form_data(request)
    try:
        client = str(uuid.UUID(values.get('client_id', '')))
    except ValueError as exc:
        raise oauth_error('invalid_client') from exc
    rest('POST', 'rpc/open_tgate_revoke_oauth_token',
         body={'client': client, 'digest': key_hash(values.get('token', ''))})
    return JSONResponse({}, headers=NO_STORE)
