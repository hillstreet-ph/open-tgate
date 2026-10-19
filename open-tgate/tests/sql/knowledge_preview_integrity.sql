-- Run after knowledge-preview migration as migration administrator. Fixtures roll back.
begin;
do $$
declare
  unicode_source uuid := gen_random_uuid();
  original_content text := repeat('界', 100000);
  preview record;
  first_page uuid[];
  second_page uuid[];
  row_count integer;
begin
  insert into public.open_tgate_knowledge_sources (id, title, content, approved, enabled, created_at)
    values (unicode_source, 'Rollback-only Unicode preview', original_content, true, true, now() + interval '101 years');
  -- Fixture rows sort ahead of real data and span more than two pages.
  insert into public.open_tgate_knowledge_sources (title, content, approved, enabled, created_at)
    select 'Rollback-only preview page ' || series, 'bounded preview facts', true, true, now() + interval '100 years'
    from generate_series(1, 205) as series;

  select * into preview from public.open_tgate_list_knowledge_previews(100, 0) where id = unicode_source;
  assert preview.id = unicode_source, 'Newest Unicode fixture was absent from list';
  assert char_length(preview.content) = 180, 'Preview must be bounded by Unicode characters';
  assert preview.content = repeat('界', 180), 'Preview damaged UTF-8 source contents';
  assert (select char_length(content) = 100000 from public.open_tgate_knowledge_sources where id = unicode_source),
    'Preview listing modified source data';
  assert (select char_length(to_jsonb(preview)::text) < 2000), 'Preview response included full content';

  select array_agg(id order by created_at desc, id asc) into first_page
    from public.open_tgate_list_knowledge_previews(100, 0);
  select array_agg(id order by created_at desc, id asc) into second_page
    from public.open_tgate_list_knowledge_previews(100, 100);
  assert cardinality(first_page) = 100 and cardinality(second_page) = 100, 'Bounded pages were incomplete';
  assert not first_page && second_page, 'Stable knowledge pages repeated source IDs';
  assert (select array_agg(id order by created_at desc, id asc) = first_page
    from public.open_tgate_list_knowledge_previews(100, -10)), 'Negative service offset did not clamp safely';
  select count(*) into row_count from public.open_tgate_list_knowledge_previews(999999, 0);
  assert row_count = 101, 'Service request exceeded maximum 101-row page';
  assert (select count(*) = 1 from public.open_tgate_list_knowledge_previews(0, 0)),
    'Zero service limit did not clamp safely';

  assert not has_function_privilege('anon', 'public.open_tgate_list_knowledge_previews(integer,integer)', 'execute'),
    'Anonymous callers can list shared workspace previews';
  assert not has_function_privilege('authenticated', 'public.open_tgate_list_knowledge_previews(integer,integer)', 'execute'),
    'Authenticated callers can bypass API operator verification';
  assert has_function_privilege('service_role', 'public.open_tgate_list_knowledge_previews(integer,integer)', 'execute'),
    'API service cannot list bounded previews';
end;
$$;
rollback;
