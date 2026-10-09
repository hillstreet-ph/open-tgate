-- Real database replay/expiry/owner and revocation checks; all writes roll back.
begin;
do $$
declare c uuid:=gen_random_uuid(); u uuid; email text; r jsonb; k uuid;
 code_digest text:=md5(gen_random_uuid()::text)||md5(gen_random_uuid()::text);
 access_digest text:=md5(gen_random_uuid()::text)||md5(gen_random_uuid()::text);
 refresh_digest text:=md5(gen_random_uuid()::text)||md5(gen_random_uuid()::text);
 next_access text:=md5(gen_random_uuid()::text)||md5(gen_random_uuid()::text);
 next_refresh text:=md5(gen_random_uuid()::text)||md5(gen_random_uuid()::text);
begin
 select users.id,users.email into u,email from auth.users users join public.open_tgate_operators op on lower(op.email)=lower(users.email) where op.is_active limit 1;
 assert u is not null,'An active operator is required for the rollback-only OAuth fixture';
 perform public.open_tgate_register_oauth_client(c,'rollback-only OAuth fixture',array['https://chatgpt.com/connector/oauth/fixture']);
 insert into public.open_tgate_oauth_codes(code_hash,client_id,redirect_uri,challenge,resource,scopes,created_by,owner_email,client_name)
 values(code_digest,c,'https://chatgpt.com/connector/oauth/fixture',repeat('a',43),'https://open-tgate.site/mcp',array['read'],u,email,'rollback-only OAuth fixture');
 r:=public.open_tgate_exchange_oauth_code(c,'https://wrong.test/mcp',access_digest,'otg_fixture',refresh_digest,code_digest,repeat('a',43),'https://chatgpt.com/connector/oauth/fixture');
 assert r is null,'Wrong resource exchanged a code';
 r:=public.open_tgate_exchange_oauth_code(c,'https://open-tgate.site/mcp',access_digest,'otg_fixture',refresh_digest,code_digest,repeat('b',43),'https://chatgpt.com/connector/oauth/fixture');
 assert r is null,'Wrong PKCE exchanged a code';
 r:=public.open_tgate_exchange_oauth_code(c,'https://open-tgate.site/mcp',access_digest,'otg_fixture',refresh_digest,code_digest,repeat('a',43),'https://chatgpt.com/connector/oauth/fixture');
 assert r->'scopes'='["read"]'::jsonb,'Valid code exchange failed';
 r:=public.open_tgate_exchange_oauth_code(c,'https://open-tgate.site/mcp',access_digest,'otg_fixture',refresh_digest,code_digest,repeat('a',43),'https://chatgpt.com/connector/oauth/fixture');
 assert r is null,'Authorization code replay minted access';
 select id into k from public.open_tgate_api_keys where token_hash=access_digest;
 assert (select expires_at between now()+interval '59 minutes' and now()+interval '61 minutes' from public.open_tgate_api_keys where id=k),'Access token expiry is incorrect';
 r:=public.open_tgate_refresh_oauth_token(gen_random_uuid(),'https://open-tgate.site/mcp',next_access,'otg_fixture',next_refresh,refresh_digest);
 assert r is null,'Another client refreshed the grant';
 r:=public.open_tgate_refresh_oauth_token(c,'https://open-tgate.site/mcp',next_access,'otg_fixture',next_refresh,refresh_digest);
 assert r is not null,'Valid refresh failed';
 r:=public.open_tgate_refresh_oauth_token(c,'https://open-tgate.site/mcp',access_digest,'otg_fixture',refresh_digest,refresh_digest);
 assert r is null,'Refresh replay succeeded';
 perform public.open_tgate_revoke_oauth_token(c,next_refresh);
 assert (select revoked_at is not null from public.open_tgate_api_keys where id=k),'Refresh revocation failed';
 r:=public.open_tgate_refresh_oauth_token(c,'https://open-tgate.site/mcp',access_digest,'otg_fixture',refresh_digest,next_refresh);
 assert r is null,'Revoked grant refreshed';
 assert not has_table_privilege('anon','public.open_tgate_oauth_codes','SELECT'),'Anonymous code access';
 assert not has_function_privilege('authenticated','public.open_tgate_exchange_oauth_code(uuid,text,text,text,text,text,text,text)','EXECUTE'),'Browser can exchange codes directly through RPC';
end $$;
rollback;
