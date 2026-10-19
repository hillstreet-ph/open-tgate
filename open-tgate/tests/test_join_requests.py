"""Consent-only join-request storage and API boundary regressions."""

from pathlib import Path


MIGRATION = (
    Path(__file__).resolve().parents[1]
    / "supabase/migrations/20261013224038_open_tgate_join_request_read.sql"
)


def test_join_request_storage_is_service_only_and_opaque_to_readers():
    sql = MIGRATION.read_text()
    assert "request_ref uuid primary key default gen_random_uuid()" in sql
    assert "requester_tg_id text not null" in sql
    assert "unique (account_id, chat_id, requester_tg_id)" in sql
    assert "alter table public.open_tgate_join_requests enable row level security" in sql
    assert "revoke all on public.open_tgate_join_requests from public, anon, authenticated" in sql
    assert "grant all on public.open_tgate_join_requests to service_role" in sql
    assert "grant select on public.open_tgate_join_requests to authenticated" not in sql


def test_join_request_read_slice_cannot_approve_or_add_members():
    sql = MIGRATION.read_text().lower()
    for forbidden in (
        "approvechatjoinrequest",
        "processchatjoinrequest",
        "addchatmember",
        "addchatmembers",
        "invite_link",
        "phone",
        "username",
        "bio",
    ):
        assert forbidden not in sql
