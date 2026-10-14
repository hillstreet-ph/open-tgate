-- Rollback-only verification of scope expansion, existing defaults and redacted auditing.
begin;
do $$
declare mutation jsonb; u uuid; email text; k uuid; source uuid; a uuid; code_id uuid:=gen_random_uuid();
        all_scopes text[]:=array['read','knowledge:read','knowledge:write','ai:draft','accounts:write'];
        digest text:=md5(gen_random_uuid()::text)||md5(gen_random_uuid()::text); n integer;
begin
  select users.id,users.email into u,email from auth.users users
    join public.open_tgate_operators op on lower(op.email)=lower(users.email) where op.is_active limit 1;
  assert u is not null,'An active operator is required for the rollback-only fixture';
  insert into public.open_tgate_api_keys(created_by,owner_email,name,prefix,token_hash)
    values(u,email,'rollback-only scope fixture','otg_fixture',digest) returning id into k;
  assert (select scopes=array['read','knowledge:read'] from public.open_tgate_api_keys where id=k),'Existing read-only defaults changed';
  update public.open_tgate_api_keys set scopes=all_scopes where id=k;
  assert (select scopes=all_scopes from public.open_tgate_api_keys where id=k),'Opt-in scopes rejected';
  begin
    update public.open_tgate_api_keys set scopes=array['send'] where id=k;
    raise exception 'Unimplemented send scope accepted';
  exception when check_violation then null; end;
  begin
    update public.open_tgate_api_keys set scopes=array[]::text[] where id=k;
    raise exception 'Empty scope accepted';
  exception when check_violation then null; end;
  insert into public.open_tgate_oauth_clients(client_id,client_name,redirect_uris)
    values(code_id,'rollback-only scope fixture',array['https://chatgpt.com/callback']);
  insert into public.open_tgate_oauth_codes(code_hash,client_id,redirect_uri,challenge,resource,scopes,created_by,owner_email,client_name)
    values(digest,code_id,'https://chatgpt.com/callback',repeat('a',43),'https://open-tgate.site/mcp',all_scopes,u,email,'rollback-only scope fixture');
  begin
    update public.open_tgate_oauth_codes set scopes=array['operator'] where code_hash=digest;
    raise exception 'OAuth privilege escalation accepted';
  exception when check_violation then null; end;
  mutation:=public.open_tgate_mutate_workspace('knowledge','INSERT',null,
    '{"title":"private fixture title","content":"private fixture content"}'::jsonb,u,k);
  source:=(mutation->0->>'id')::uuid;
  perform public.open_tgate_mutate_workspace('knowledge','UPDATE',source,
    '{"content":"revised private fixture content","approved":true}'::jsonb,u,k);
  perform public.open_tgate_mutate_workspace('knowledge','DELETE',source,'{}'::jsonb,u,k);
  select count(*) into n from public.open_tgate_workspace_audit_events where resource_id=source and resource='knowledge';
  assert n=3,'Knowledge mutation audit is incomplete';
  assert not exists(select 1 from public.open_tgate_workspace_audit_events where resource_id=source
    and (actor_user_id is distinct from u or actor_key_id is distinct from k)),'Mutation attribution lost';
  begin
    perform public.open_tgate_mutate_workspace('account_label','UPDATE',gen_random_uuid(),'{"label":"T","status":"closed"}'::jsonb,u,k);
    raise exception 'RPC accepted unsupported account field';
  exception when invalid_parameter_value then null; end;
  assert not has_function_privilege('authenticated','public.open_tgate_mutate_workspace(text,text,uuid,jsonb,uuid,uuid)','EXECUTE'),'Browser may impersonate mutation actor';
  assert not exists(select 1 from public.open_tgate_workspace_audit_events e
    where resource_id=source and to_jsonb(e)::text like '%private fixture%'),'Audit leaked knowledge content';
  assert exists(select 1 from public.open_tgate_workspace_audit_events
    where resource_id=source and operation='UPDATE' and changed_fields=array['approved','content']),'Audit fields incorrect';
  select id into a from public.open_tgate_tg_accounts limit 1;
  if a is not null then
    select count(*) into n from public.open_tgate_workspace_audit_events where resource_id=a;
    update public.open_tgate_tg_accounts set label='rollback-only private label' where id=a;
    assert (select count(*)=n+1 from public.open_tgate_workspace_audit_events where resource_id=a),'Label change missing audit';
  end if;
  assert not has_table_privilege('anon','public.open_tgate_workspace_audit_events','SELECT'),'Anonymous audit access';
  assert not has_table_privilege('authenticated','public.open_tgate_workspace_audit_events','INSERT'),'Browser audit write';
  assert not has_table_privilege('service_role','public.open_tgate_workspace_audit_events','UPDATE'),'Audit records mutable';
  assert not has_table_privilege('service_role','public.open_tgate_workspace_audit_events','DELETE'),'Audit records deletable';
end $$;
rollback;
