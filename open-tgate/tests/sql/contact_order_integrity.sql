-- Run after the contact-order migration as migration administrator.
-- Actual-table EXPLAIN validates deployed indexes without reading private rows.
-- A temporary clone copies those index definitions and exercises the natural
-- planner against 32,000 contacts and 48,000 other entities across eight accounts.
-- Production rows and statistics are unchanged; all temporary state rolls back.
begin;
do $$
declare
  plan jsonb;
  description text;
  previous_seqscan text := current_setting('enable_seqscan');
  previous_bitmapscan text := current_setting('enable_bitmapscan');
  previous_sort text := current_setting('enable_sort');
begin
  -- Tiny live tables may rationally prefer a sequential scan. Check that the
  -- ordered index path exists independently of the current live row count.
  perform set_config('enable_seqscan', 'off', true);
  perform set_config('enable_bitmapscan', 'off', true);
  -- A tiny live table can also prefer scanning the cheaper account index and
  -- sorting all rows. Discourage that alternative only for this availability
  -- check; natural sort/index costing is restored for the large fixture below.
  perform set_config('enable_sort', 'off', true);
  execute 'explain (format json) select account_id, tg_id, title, username, meta
    from public.open_tgate_tg_entities where kind = ''contact''
    order by title asc nulls last, tg_id asc, account_id asc limit 100 offset 100' into plan;
  description := plan::text;
  assert description like '%open_tgate_contacts_global_order_idx%', 'Global contacts lack an ordered partial index';
  assert description not like '%"Node Type": "Sort"%', 'Global contacts require an explicit sort';
  assert description not like '%"Node Type": "Incremental Sort"%', 'Global contacts require an incremental sort';

  execute 'explain (format json) select account_id, tg_id, title, username, meta
    from public.open_tgate_tg_entities where kind = ''contact''
    and account_id = ''00000000-0000-0000-0000-000000000001''::uuid
    order by title asc nulls last, tg_id asc, account_id asc limit 100 offset 100' into plan;
  description := plan::text;
  assert description like '%open_tgate_contacts_account_order_idx%', 'Account contacts lack an ordered partial index';
  assert description not like '%"Node Type": "Sort"%', 'Account contacts require an explicit sort';
  assert description not like '%"Node Type": "Incremental Sort"%', 'Account contacts require an incremental sort';
  perform set_config('enable_seqscan', previous_seqscan, true);
  perform set_config('enable_bitmapscan', previous_bitmapscan, true);
  perform set_config('enable_sort', previous_sort, true);
end;
$$;

-- INCLUDING INDEXES copies the actual migration definitions, rather than
-- restating them in the fixture. LIKE does not copy foreign keys or RLS.
create temporary table open_tgate_contact_order_fixture
  (like public.open_tgate_tg_entities including defaults including constraints including indexes)
  on commit drop;
insert into open_tgate_contact_order_fixture (account_id, kind, tg_id, title)
  select md5(((series - 1) % 8 + 1)::text)::uuid,
    case when series <= 32000 then 'contact' else 'user' end,
    series::text,
    case when series % 100 = 0 then null else 'Contact ' || lpad((series % 97)::text, 3, '0') end
  from generate_series(1, 80000) as series;
analyze open_tgate_contact_order_fixture;
do $$
declare
  global_index text;
  account_index text;
  fixture_account uuid := md5('1')::uuid;
  plan jsonb;
  description text;
begin
  -- Resolve cloned index names by their actual key columns and partial contact
  -- predicate. Names are assigned by LIKE and differ from the production names.
  select indexrelid::regclass::text into global_index from pg_index idx
    where idx.indrelid = 'open_tgate_contact_order_fixture'::regclass
    and pg_get_expr(idx.indpred, idx.indrelid) = '(kind = ''contact''::text)'
    and array(select att.attname::text from unnest(idx.indkey) with ordinality as key(attnum, position)
      join pg_attribute att on att.attrelid = idx.indrelid and att.attnum = key.attnum
      order by key.position) = array['title', 'tg_id', 'account_id'];
  select indexrelid::regclass::text into account_index from pg_index idx
    where idx.indrelid = 'open_tgate_contact_order_fixture'::regclass
    and pg_get_expr(idx.indpred, idx.indrelid) = '(kind = ''contact''::text)'
    and array(select att.attname::text from unnest(idx.indkey) with ordinality as key(attnum, position)
      join pg_attribute att on att.attrelid = idx.indrelid and att.attnum = key.attnum
      order by key.position) = array['account_id', 'title', 'tg_id'];
  assert global_index is not null and account_index is not null, 'Missing cloned contact-only indexes';

  execute 'explain (analyze, format json, timing off) select account_id, tg_id, title, username, meta
    from open_tgate_contact_order_fixture where kind = ''contact''
    order by title asc nulls last, tg_id asc, account_id asc limit 100 offset 100' into plan;
  description := plan::text;
  assert description like '%' || global_index || '%', 'Natural global contact plan missed the ordered index';
  assert description not like '%"Node Type": "Sort"%', 'Natural global contact plan sorts';
  assert description not like '%"Node Type": "Incremental Sort"%', 'Natural global contact plan incrementally sorts';
  assert (plan->0->'Plan'->>'Actual Rows')::integer = 100, 'Global contact page did not execute 100 rows';

  execute format('explain (analyze, format json, timing off) select account_id, tg_id, title, username, meta
    from open_tgate_contact_order_fixture where kind = ''contact'' and account_id = %L::uuid
    order by title asc nulls last, tg_id asc, account_id asc limit 100 offset 100', fixture_account) into plan;
  description := plan::text;
  assert description like '%' || account_index || '%', 'Natural account contact plan missed the ordered index';
  assert description not like '%"Node Type": "Sort"%', 'Natural account contact plan sorts';
  assert description not like '%"Node Type": "Incremental Sort"%', 'Natural account contact plan incrementally sorts';
  assert (plan->0->'Plan'->>'Actual Rows')::integer = 100, 'Account contact page did not execute 100 rows';
end;
$$;
rollback;
