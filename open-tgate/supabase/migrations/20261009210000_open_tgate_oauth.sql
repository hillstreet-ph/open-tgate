-- Additive OAuth storage alongside the existing public Open-TGate API tables.
-- Existing keys keep their current scope and no expiration; OAuth keys expire.
alter table public.open_tgate_api_keys add column if not exists expires_at timestamptz;
create table public.open_tgate_oauth_clients (
 client_id uuid primary key, client_name text not null, redirect_uris text[] not null,
 created_at timestamptz not null default now()
);
create table public.open_tgate_oauth_codes (
 code_hash text primary key check(length(code_hash)=64),
 client_id uuid not null references public.open_tgate_oauth_clients(client_id) on delete cascade,
 redirect_uri text not null, challenge text not null, resource text not null, scopes text[] not null,
 created_by uuid not null references auth.users(id) on delete cascade,
 owner_email text not null, client_name text not null,
 expires_at timestamptz not null default now()+interval '5 minutes'
);
create table public.open_tgate_oauth_refresh (
 token_hash text primary key check(length(token_hash)=64),
 client_id uuid not null references public.open_tgate_oauth_clients(client_id) on delete cascade,
 key_id uuid not null references public.open_tgate_api_keys(id) on delete cascade,
 resource text not null, consumed_at timestamptz,
 expires_at timestamptz not null default now()+interval '30 days'
);
create index on public.open_tgate_oauth_clients(created_at);
create index on public.open_tgate_oauth_codes(expires_at);
create index on public.open_tgate_oauth_refresh(key_id);
create index on public.open_tgate_oauth_refresh(client_id);
alter table public.open_tgate_oauth_clients enable row level security;
alter table public.open_tgate_oauth_codes enable row level security;
alter table public.open_tgate_oauth_refresh enable row level security;
revoke all on public.open_tgate_oauth_clients,public.open_tgate_oauth_codes,public.open_tgate_oauth_refresh from public,anon,authenticated;
grant all on public.open_tgate_oauth_clients,public.open_tgate_oauth_codes,public.open_tgate_oauth_refresh to service_role;

create function public.open_tgate_register_oauth_client(client uuid,client_label text,redirects text[])
returns boolean language plpgsql security invoker set search_path='' as $$
begin
 perform pg_advisory_xact_lock(7474001);
 -- Shared admission control also covers direct API calls and concurrent replicas.
 -- At most 300 abandoned registrations can accumulate before the 30-minute prune.
 if (select count(*) from public.open_tgate_oauth_clients
     where created_at > now()-interval '1 minute') >= 10 then return false; end if;
 delete from public.open_tgate_oauth_codes where expires_at < now();
 delete from public.open_tgate_oauth_refresh where expires_at < now();
 delete from public.open_tgate_oauth_clients c where c.created_at < now()-interval '30 minutes'
 and not exists(select 1 from public.open_tgate_oauth_codes x where x.client_id=c.client_id)
 and not exists(select 1 from public.open_tgate_oauth_refresh x where x.client_id=c.client_id);
 if (select count(*) from public.open_tgate_oauth_clients)>=1000 then return false; end if;
 insert into public.open_tgate_oauth_clients values(client,client_label,redirects,now());
 return true;
end $$;

create function public.open_tgate_exchange_oauth_code(client uuid,target text,access_hash text,access_prefix text,refresh_hash text,code_digest text,pkce text,redirect text)
returns jsonb language plpgsql security invoker set search_path='' as $$
declare c public.open_tgate_oauth_codes; k uuid;
begin
 -- Delete/return is atomic. Concurrent replay cannot mint another key.
 delete from public.open_tgate_oauth_codes where code_hash=code_digest and client_id=client
 and resource=target and challenge=pkce and redirect_uri=redirect and expires_at>now()
 returning * into c;
 if c.code_hash is null or not public.open_tgate_api_key_owner_active(c.owner_email) then return null; end if;
 insert into public.open_tgate_api_keys(created_by,owner_email,name,prefix,token_hash,scopes,expires_at)
 values(c.created_by,c.owner_email,'OAuth: '||left(c.client_name,73),access_prefix,access_hash,c.scopes,now()+interval '1 hour') returning id into k;
 insert into public.open_tgate_oauth_refresh(token_hash,client_id,key_id,resource) values(refresh_hash,client,k,target);
 return jsonb_build_object('scopes',c.scopes);
end $$;

create function public.open_tgate_refresh_oauth_token(client uuid,target text,access_hash text,access_prefix text,refresh_hash text,previous_hash text)
returns jsonb language plpgsql security invoker set search_path='' as $$
declare r public.open_tgate_oauth_refresh; k public.open_tgate_api_keys;
begin
 select * into r from public.open_tgate_oauth_refresh where token_hash=previous_hash and client_id=client
 and resource=target and expires_at>now() for update;
 if r.key_id is null then return null; end if;
 -- Keep consumed hashes until fixed family expiry. Replay invalidates the grant,
 -- including any successor an attacker might have won in a rotation race.
 if r.consumed_at is not null then
  update public.open_tgate_api_keys set revoked_at=now() where id=r.key_id;
  return null;
 end if;
 update public.open_tgate_oauth_refresh set consumed_at=now() where token_hash=r.token_hash;
 select * into k from public.open_tgate_api_keys where id=r.key_id for update;
 if k.revoked_at is not null or not public.open_tgate_api_key_owner_active(k.owner_email) then return null; end if;
 update public.open_tgate_api_keys set token_hash=access_hash,prefix=access_prefix,expires_at=now()+interval '1 hour' where id=k.id;
 insert into public.open_tgate_oauth_refresh(token_hash,client_id,key_id,resource,expires_at)
 values(refresh_hash,client,k.id,target,r.expires_at);
 return jsonb_build_object('scopes',k.scopes);
end $$;

create function public.open_tgate_revoke_oauth_token(client uuid,digest text)
returns void language plpgsql security invoker set search_path='' as $$
begin
 update public.open_tgate_api_keys k set revoked_at=now()
 where exists(select 1 from public.open_tgate_oauth_refresh r where r.key_id=k.id and r.client_id=client
 and (r.token_hash=digest or k.token_hash=digest));
end $$;
revoke all on function public.open_tgate_register_oauth_client(uuid,text,text[]),public.open_tgate_exchange_oauth_code(uuid,text,text,text,text,text,text,text),public.open_tgate_refresh_oauth_token(uuid,text,text,text,text,text),public.open_tgate_revoke_oauth_token(uuid,text) from public,anon,authenticated;
grant execute on function public.open_tgate_register_oauth_client(uuid,text,text[]),public.open_tgate_exchange_oauth_code(uuid,text,text,text,text,text,text,text),public.open_tgate_refresh_oauth_token(uuid,text,text,text,text,text),public.open_tgate_revoke_oauth_token(uuid,text) to service_role;
notify pgrst,'reload schema';
