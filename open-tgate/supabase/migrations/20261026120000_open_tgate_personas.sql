-- AI draft personas: named voice/role instructions an operator selects when
-- drafting a reply. Config only — a persona never sends a message and is never
-- a Telegram credential. Drafting stays scoped to ai:draft and still never sends.
create table if not exists public.open_tgate_personas (
  id uuid primary key default gen_random_uuid(),
  name text not null check (char_length(btrim(name)) between 1 and 80),
  instructions text not null check (char_length(instructions) between 1 and 4000),
  enabled boolean not null default true,
  created_by uuid references auth.users(id) on delete set null,
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now()
);
alter table public.open_tgate_personas enable row level security;
revoke all on public.open_tgate_personas from anon, authenticated;
grant select on public.open_tgate_personas to authenticated;
grant all on public.open_tgate_personas to service_role;
drop policy if exists open_tgate_personas_operator_read on public.open_tgate_personas;
create policy open_tgate_personas_operator_read on public.open_tgate_personas
for select to authenticated using (public.open_tgate_is_operator());
comment on table public.open_tgate_personas is
  'Operator-defined AI draft personas (voice/role). Config only; a persona never sends a message and is never a Telegram credential.';

-- Keep updated_at honest on edits.
create or replace function public.open_tgate_touch_persona()
returns trigger language plpgsql security definer set search_path='' as $$
begin new.updated_at = now(); return new; end $$;
revoke all on function public.open_tgate_touch_persona() from public, anon, authenticated;
drop trigger if exists open_tgate_personas_touch on public.open_tgate_personas;
create trigger open_tgate_personas_touch before update on public.open_tgate_personas
  for each row execute function public.open_tgate_touch_persona();

-- The workspace audit ledger now also records persona mutations (field names only).
alter table public.open_tgate_workspace_audit_events
  drop constraint if exists open_tgate_workspace_audit_events_resource_check;
alter table public.open_tgate_workspace_audit_events
  add constraint open_tgate_workspace_audit_events_resource_check
  check (resource in ('knowledge','account_label','persona'));

-- Recreate the capture trigger with a persona branch. Still stores field names
-- only — never persona text, source content, label, credential or payload.
create or replace function public.open_tgate_capture_workspace_mutation()
returns trigger language plpgsql security definer set search_path='' as $$
declare resource_name text; record_id uuid; fields text[]; old_row jsonb; new_row jsonb;
begin
  if tg_op='DELETE' then record_id=old.id; old_row=to_jsonb(old); new_row='{}'::jsonb;
  elsif tg_op='INSERT' then record_id=new.id; old_row='{}'::jsonb; new_row=to_jsonb(new);
  else record_id=new.id; old_row=to_jsonb(old); new_row=to_jsonb(new); end if;
  if tg_table_name='open_tgate_tg_accounts' then
    if new.label is not distinct from old.label then return new; end if;
    resource_name='account_label'; fields=array['label'];
  elsif tg_table_name='open_tgate_personas' then
    resource_name='persona';
    select coalesce(array_agg(f order by f),array[]::text[]) into fields
    from unnest(array['name','instructions','enabled']) f
    where old_row->f is distinct from new_row->f;
    if cardinality(fields)=0 and tg_op='UPDATE' then return new; end if;
  else
    resource_name='knowledge';
    select coalesce(array_agg(f order by f),array[]::text[]) into fields
    from unnest(array['title','content','source_type','approved','enabled']) f
    where old_row->f is distinct from new_row->f;
    if cardinality(fields)=0 and tg_op='UPDATE' then return new; end if;
  end if;
  insert into public.open_tgate_workspace_audit_events(resource,resource_id,operation,changed_fields,actor_user_id,actor_key_id)
    values(resource_name,record_id,tg_op,fields,
      coalesce(nullif(current_setting('open_tgate.actor_user',true),'')::uuid,auth.uid()),
      nullif(current_setting('open_tgate.actor_key',true),'')::uuid);
  if tg_op='DELETE' then return old; end if;
  return new;
end $$;
revoke all on function public.open_tgate_capture_workspace_mutation() from public,anon,authenticated;
drop trigger if exists open_tgate_personas_mutation_audit on public.open_tgate_personas;
create trigger open_tgate_personas_mutation_audit after insert or update or delete
  on public.open_tgate_personas for each row execute function public.open_tgate_capture_workspace_mutation();

-- Recreate the workspace mutation RPC with a persona branch. Same transaction-bound
-- actor context and the same validation discipline as knowledge/account_label.
create or replace function public.open_tgate_mutate_workspace(resource_name text,operation_name text,record_id uuid,
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
  elsif resource_name='persona' then
    if payload - array['name','instructions','enabled'] <> '{}'::jsonb
      or exists(select 1 from jsonb_each(payload) e where
        (e.key in ('name','instructions') and jsonb_typeof(e.value)<>'string')
        or (e.key='enabled' and jsonb_typeof(e.value)<>'boolean')) then
      raise exception 'invalid_persona_fields' using errcode='22023';
    end if;
    if operation_name='INSERT' and record_id is null then
      if char_length(btrim(coalesce(payload->>'name','')))=0 or char_length(coalesce(payload->>'instructions',''))=0 then
        raise exception 'invalid_persona_fields' using errcode='22023';
      end if;
      with changed as (insert into public.open_tgate_personas(name,instructions,enabled,created_by)
        values(btrim(payload->>'name'),payload->>'instructions',coalesce((payload->>'enabled')::boolean,true),actor_user)
        returning *) select jsonb_agg(to_jsonb(changed)) into result from changed;
    elsif operation_name='UPDATE' and record_id is not null and payload<>'{}'::jsonb then
      with changed as (update public.open_tgate_personas set
        name=coalesce(btrim(payload->>'name'),name),instructions=coalesce(payload->>'instructions',instructions),
        enabled=coalesce((payload->>'enabled')::boolean,enabled)
        where id=record_id returning *) select jsonb_agg(to_jsonb(changed)) into result from changed;
    elsif operation_name='DELETE' and record_id is not null and payload='{}'::jsonb then
      with changed as (delete from public.open_tgate_personas where id=record_id returning id)
        select jsonb_agg(to_jsonb(changed)) into result from changed;
    else raise exception 'invalid_persona_mutation' using errcode='22023'; end if;
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
