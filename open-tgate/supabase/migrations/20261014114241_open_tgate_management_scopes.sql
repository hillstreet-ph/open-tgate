-- Opt-in workspace management. Existing rows and read-only defaults are unchanged.
-- OAuth access tokens use open_tgate_api_keys; refresh grants retain those scopes.
alter table public.open_tgate_api_keys drop constraint open_tgate_api_keys_scopes_check;
alter table public.open_tgate_api_keys add constraint open_tgate_api_keys_scopes_check
  check (scopes <@ array['read','knowledge:read','knowledge:write','ai:draft','accounts:write']::text[]
         and cardinality(scopes)>0 and array_position(scopes,null) is null);
alter table public.open_tgate_oauth_codes add constraint open_tgate_oauth_codes_scopes_check
  check (scopes <@ array['read','knowledge:read','knowledge:write','ai:draft','accounts:write']::text[]
         and cardinality(scopes)>0 and array_position(scopes,null) is null);
comment on table public.open_tgate_api_keys is
  'Hashed integration credentials with explicit scopes. Existing defaults stay read-only. Management grants never confer operator or Telegram login/send access.';

-- The existing Telegram audit requires an account ID. Knowledge has no account,
-- so use a separate immutable, content-free workspace mutation ledger.
create table public.open_tgate_workspace_audit_events (
  id bigint generated always as identity primary key,
  resource text not null check (resource in ('knowledge','account_label')),
  resource_id uuid not null,
  actor_user_id uuid,
  actor_key_id uuid,
  operation text not null check (operation in ('INSERT','UPDATE','DELETE')),
  changed_fields text[] not null,
  observed_at timestamptz not null default now()
);
alter table public.open_tgate_workspace_audit_events enable row level security;
revoke all on public.open_tgate_workspace_audit_events from public,anon,authenticated,service_role;
grant select,insert on public.open_tgate_workspace_audit_events to service_role;
revoke all on sequence public.open_tgate_workspace_audit_events_id_seq from public,anon,authenticated,service_role;
grant usage,select on sequence public.open_tgate_workspace_audit_events_id_seq to service_role;

create function public.open_tgate_capture_workspace_mutation()
returns trigger language plpgsql security definer set search_path='' as $$
declare resource_name text; record_id uuid; fields text[]; old_row jsonb; new_row jsonb;
begin
  if tg_op='DELETE' then record_id=old.id; old_row=to_jsonb(old); new_row='{}'::jsonb;
  elsif tg_op='INSERT' then record_id=new.id; old_row='{}'::jsonb; new_row=to_jsonb(new);
  else record_id=new.id; old_row=to_jsonb(old); new_row=to_jsonb(new); end if;
  if tg_table_name='open_tgate_tg_accounts' then
    if new.label is not distinct from old.label then return new; end if;
    resource_name='account_label'; fields=array['label'];
  else
    resource_name='knowledge';
    select coalesce(array_agg(f order by f),array[]::text[]) into fields
    from unnest(array['title','content','source_type','approved','enabled']) f
    where old_row->f is distinct from new_row->f;
    if cardinality(fields)=0 and tg_op='UPDATE' then return new; end if;
  end if;
  -- Deliberately store field names only: no source text, label, credential or payload.
  insert into public.open_tgate_workspace_audit_events(resource,resource_id,operation,changed_fields,actor_user_id,actor_key_id)
    values(resource_name,record_id,tg_op,fields,
      coalesce(nullif(current_setting('open_tgate.actor_user',true),'')::uuid,auth.uid()),
      nullif(current_setting('open_tgate.actor_key',true),'')::uuid);
  if tg_op='DELETE' then return old; end if;
  return new;
end $$;
revoke all on function public.open_tgate_capture_workspace_mutation() from public,anon,authenticated;
create trigger open_tgate_knowledge_mutation_audit after insert or update or delete
  on public.open_tgate_knowledge_sources for each row execute function public.open_tgate_capture_workspace_mutation();
create trigger open_tgate_account_label_mutation_audit after update of label
  on public.open_tgate_tg_accounts for each row execute function public.open_tgate_capture_workspace_mutation();
-- Called only by the API service after its verified principal/scope checks.
-- Actor context and writes share one transaction; arbitrary API key bodies cannot
-- set actor identity, created_by, login status or Telegram credentials.
create function public.open_tgate_mutate_workspace(resource_name text,operation_name text,record_id uuid,
  payload jsonb,actor_user uuid,actor_key uuid default null)
returns jsonb language plpgsql security invoker set search_path='' as $$
declare result jsonb;
begin
  if actor_user is null or jsonb_typeof(payload) is distinct from 'object' then
    raise exception 'invalid_mutation' using errcode='22023';
  end if;
  if actor_key is not null and not exists(select 1 from public.open_tgate_api_keys
    where id=actor_key and created_by=actor_user and revoked_at is null
      and (expires_at is null or expires_at>now())) then
    raise exception 'invalid_actor_key' using errcode='42501';
  end if;
  perform set_config('open_tgate.actor_user',actor_user::text,true);
  perform set_config('open_tgate.actor_key',coalesce(actor_key::text,''),true);
  if resource_name='knowledge' then
    if payload - array['title','content','source_type','approved','enabled'] <> '{}'::jsonb
      or exists(select 1 from jsonb_each(payload) e where
        (e.key in ('title','content','source_type') and jsonb_typeof(e.value)<>'string')
        or (e.key in ('approved','enabled') and jsonb_typeof(e.value)<>'boolean')) then
      raise exception 'invalid_knowledge_fields' using errcode='22023';
    end if;
    if operation_name='INSERT' and record_id is null then
      with changed as (insert into public.open_tgate_knowledge_sources(title,content,source_type,approved,enabled,created_by)
        values(payload->>'title',payload->>'content',coalesce(payload->>'source_type','text'),
          coalesce((payload->>'approved')::boolean,false),coalesce((payload->>'enabled')::boolean,true),actor_user)
        returning *) select jsonb_agg(to_jsonb(changed)) into result from changed;
    elsif operation_name='UPDATE' and record_id is not null and payload<>'{}'::jsonb and not (payload ? 'source_type') then
      with changed as (update public.open_tgate_knowledge_sources set
        title=coalesce(payload->>'title',title),content=coalesce(payload->>'content',content),
        approved=coalesce((payload->>'approved')::boolean,approved),enabled=coalesce((payload->>'enabled')::boolean,enabled)
        where id=record_id returning *) select jsonb_agg(to_jsonb(changed)) into result from changed;
    elsif operation_name='DELETE' and record_id is not null and payload='{}'::jsonb then
      with changed as (delete from public.open_tgate_knowledge_sources where id=record_id returning id)
        select jsonb_agg(to_jsonb(changed)) into result from changed;
    else raise exception 'invalid_knowledge_mutation' using errcode='22023'; end if;
  elsif resource_name='account_label' and operation_name='UPDATE' and record_id is not null then
    if payload - 'label' <> '{}'::jsonb or jsonb_typeof(payload->'label') is distinct from 'string'
      or char_length(btrim(payload->>'label')) not between 1 and 80 then
      raise exception 'invalid_label' using errcode='22023';
    end if;
    with changed as (update public.open_tgate_tg_accounts set label=btrim(payload->>'label') where id=record_id returning id,label)
      select jsonb_agg(to_jsonb(changed)) into result from changed;
  else raise exception 'unsupported_mutation' using errcode='22023'; end if;
  return coalesce(result,'[]'::jsonb);
end $$;
revoke all on function public.open_tgate_mutate_workspace(text,text,uuid,jsonb,uuid,uuid) from public,anon,authenticated;
grant execute on function public.open_tgate_mutate_workspace(text,text,uuid,jsonb,uuid,uuid) to service_role;
notify pgrst,'reload schema';
