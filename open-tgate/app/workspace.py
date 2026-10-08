"""Shared operator workspace access, scoped integration keys and grounded retrieval."""
from __future__ import annotations

import hashlib
import hmac
import re
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
    owners = rest('GET', 'open_tgate_operators', params={
                   'is_active': 'eq.true', 'select': 'email', 'limit': 1000})
    if not any(str(owner.get('email', '')).casefold() == str(row['owner_email']).casefold()
               for owner in owners):
        raise HTTPException(403, 'operator_required')
    return Principal(str(row['created_by']), scopes=frozenset(row['scopes']))


def require_scope(principal: Principal, scope: str) -> None:
    if scope not in principal.scopes:
        raise HTTPException(403, 'insufficient_scope')


def retrieve_sources(query: str, sources: list[dict], limit: int = 5) -> list[dict]:
    """Bounded literal retrieval of approved information; content is never instructions."""
    terms = set(re.findall(r'\w+', query.casefold()))
    ranked: list[tuple[int, dict]] = []
    for source in sources:
        if not source.get('enabled') or not source.get('approved'):
            continue
        title = str(source.get('title', ''))
        content = str(source.get('content', ''))
        score = sum(1 for term in terms if term in (title + ' ' + content).casefold())
        if score:
            position = next((content.casefold().find(term) for term in sorted(terms)
                             if term in content.casefold()), 0)
            start = max(0, position - 120)
            ranked.append((score, {'id': source['id'], 'title': title,
                                  'excerpt': content[start:start + 1600]}))
    ranked.sort(key=lambda pair: pair[0], reverse=True)
    return [source for _, source in ranked[:limit]]


def knowledge_search(query: str) -> list[dict]:
    sources = rest('GET', 'open_tgate_knowledge_sources', params={
        'enabled': 'eq.true', 'approved': 'eq.true',
        'select': 'id,title,content,enabled,approved', 'order': 'created_at.desc', 'limit': 200})
    return retrieve_sources(query, sources)
