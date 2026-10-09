"""Shared operator workspace access, scoped integration keys and grounded retrieval."""
from __future__ import annotations

import hashlib
import hmac
from dataclasses import dataclass
from typing import Any

import httpx
from fastapi import Header, HTTPException

from .config import get_settings

KEY_SCOPES = frozenset({'read', 'knowledge:read'})


@dataclass(frozen=True)
class Principal:
    user_id: str
    email: str = ''
    scopes: frozenset[str] = KEY_SCOPES
    operator: bool = False


def key_hash(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def rest(method: str, table: str, *, params: dict | None = None, body: Any = None,
         token: str | None = None) -> Any:
    settings = get_settings()
    if not settings.supabase_url or not settings.supabase_secret_key:
        raise HTTPException(503, 'supabase_not_configured')
    headers = {'apikey': settings.supabase_secret_key,
               'Authorization': f'Bearer {token or settings.supabase_secret_key}',
               'Prefer': 'return=representation'}
    try:
        response = httpx.request(method, f'{settings.supabase_url.rstrip("/")}/rest/v1/{table}',
                                headers=headers, params=params, json=body, timeout=15)
        response.raise_for_status()
        return response.json() if response.content else None
    except httpx.HTTPError as exc:
        # Upstream responses can include source content; never return them to clients.
        raise HTTPException(502, 'workspace_storage_unavailable') from exc


def _bearer(authorization: str | None) -> str:
    if not authorization or not authorization.startswith('Bearer '):
        raise HTTPException(401, 'unauthorized')
    value = authorization[7:].strip()
    if not value:
        raise HTTPException(401, 'unauthorized')
    return value


def require_operator(authorization: str | None = Header(default=None)) -> Principal:
    token = _bearer(authorization)
    settings = get_settings()
    if not settings.supabase_url or not settings.supabase_secret_key:
        raise HTTPException(503, 'supabase_not_configured')
    try:
        response = httpx.get(f'{settings.supabase_url.rstrip("/")}/auth/v1/user',
                            headers={'apikey': settings.supabase_secret_key,
                                     'Authorization': f'Bearer {token}'}, timeout=15)
        response.raise_for_status()
        user = response.json()
    except httpx.HTTPStatusError as exc:
        if exc.response.status_code == 429 or exc.response.status_code >= 500:
            # Rate limits and Auth outages do not invalidate the caller's JWT.
            # Returning 401 here would make the dashboard discard a valid session.
            raise HTTPException(503, 'identity_service_unavailable') from exc
        raise HTTPException(401, 'unauthorized') from exc
    except httpx.HTTPError as exc:
        raise HTTPException(503, 'identity_service_unavailable') from exc
    if not user.get('id') or not user.get('email') or user.get('is_anonymous'):
        raise HTTPException(403, 'operator_required')
    # This existing predicate uses the caller's verified JWT and current allowlist.
    if rest('POST', 'rpc/open_tgate_is_operator', body={}, token=token) is not True:
        raise HTTPException(403, 'operator_required')
    return Principal(str(user['id']), str(user['email']), operator=True)


def require_reader(authorization: str | None = Header(default=None)) -> Principal:
    token = _bearer(authorization)
    settings = get_settings()
    if settings.api_admin_token and hmac.compare_digest(token, settings.api_admin_token):
        return Principal('admin')
    if not token.startswith('otg_'):
        return require_operator(authorization)
    rows = rest('GET', 'open_tgate_api_keys', params={'token_hash': f'eq.{key_hash(token)}',
                'revoked_at': 'is.null', 'select': 'created_by,owner_email,scopes', 'limit': 1})
    if not rows:
        raise HTTPException(401, 'unauthorized')
    row = rows[0]
    # Disabling an operator immediately disables every integration key they own.
    if rest('POST', 'rpc/open_tgate_api_key_owner_active',
            body={'owner_email': row['owner_email']}) is not True:
        raise HTTPException(403, 'operator_required')
    return Principal(str(row['created_by']), scopes=frozenset(row['scopes']))


def require_scope(principal: Principal, scope: str) -> None:
    if scope not in principal.scopes:
        raise HTTPException(403, 'insufficient_scope')


def knowledge_search(query: str) -> list[dict]:
    # Database full-text retrieval covers all approved sources, with at most
    # five bounded excerpts returned. There is no newest-200 exclusion window.
    return rest('POST', 'rpc/open_tgate_search_knowledge',
                body={'query_text': query, 'result_limit': 5})
