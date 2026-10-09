"""An omitted deploy version must never silently select an obsolete image."""
import json
import os
from pathlib import Path
import re
import subprocess
import textwrap

import pytest

WORKFLOW = Path(__file__).resolve().parents[2] / '.github/workflows/zeabur-deploy.yml'


def deployment_version_guard(image_tag: str) -> subprocess.CompletedProcess:
    workflow = WORKFLOW.read_text()
    guard = re.search(r'^          \[\[ "\$IMAGE_TAG" =~ .*$', workflow, re.MULTILINE)
    assert guard is not None
    # Execute the workflow's actual guard, rather than mirroring its regex.
    return subprocess.run(['bash', '-c', 'set -eu\n' + guard.group().strip() + '\necho version-guard-passed'],
                          env={**os.environ, 'IMAGE_TAG': image_tag}, text=True, capture_output=True, check=False)


def test_omitted_deploy_version_fails_before_any_image_change():
    workflow = WORKFLOW.read_text()
    field = re.search(r'^      image_tag:\n((?:        .*\n)+)', workflow, re.MULTILINE)
    assert field is not None
    default = re.search(r'^        default: (.*)$', field.group(1), re.MULTILINE)
    assert default is not None
    supplied_by_dispatch_when_omitted = json.loads(default.group(1))
    result = deployment_version_guard(supplied_by_dispatch_when_omitted)
    assert result.returncode != 0
    assert 'version-guard-passed' not in result.stdout
    assert 'Supply image_tag explicitly' in result.stdout


@pytest.mark.parametrize('image_tag', ['latest', '1.4', '1.4.2;echo unexpected-command', '$(echo unexpected-command)'])
def test_deploy_rejects_nonimmutable_or_executable_tag_values(image_tag):
    result = deployment_version_guard(image_tag)
    assert result.returncode != 0
    assert 'version-guard-passed' not in result.stdout
    assert 'unexpected-command' not in result.stdout


def test_deploy_accepts_an_explicit_immutable_version():
    result = deployment_version_guard('1.4.2')
    assert result.returncode == 0
    assert result.stdout.strip() == 'version-guard-passed'


def runtime_update_fixture(tmp_path, *, overrides=None, failure=""):
    """Execute the shipped shell/JQ/GraphQL updater with a local fake curl."""
    workflow = WORKFLOW.read_text()
    gql_start = workflow.index('          gql() {')
    gql_end = workflow.index('          # 1) Resolve', gql_start)
    update_start = workflow.index('          DATA="$(jq -rn')
    update_end = workflow.index('          # Baseline:', update_start)
    script = 'set -eu\n' + textwrap.dedent(workflow[gql_start:gql_end])
    script += textwrap.dedent(workflow[update_start:update_end])
    script += '\napply_runtime_variables api api-id\napply_runtime_variables worker worker-id\n'
    state = {
        role: {
            'SUPABASE_URL': 'old-url', 'TELEGRAM_API_HASH': 'old-hash',
            'SENTRY_DSN': f'{role}-sentry-secret', 'SENTRY_ENVIRONMENT': 'production',
            'OPEN_CONNECT_AI_MODEL': f'{role}-chosen-model', 'CUSTOM_SETTING': 'leave me alone',
        }
        for role in ['api-id', 'worker-id', 'unrelated-id']
    }
    state_path = tmp_path / 'state.json'
    state_path.write_text(json.dumps(state))
    requests_path = tmp_path / 'requests.jsonl'
    mock_curl = tmp_path / 'curl'
    mock_curl.write_text(textwrap.dedent(r'''
        #!/usr/bin/env python3
        import json, os, re, sys
        from pathlib import Path
        query = json.load(sys.stdin)['query']
        path = Path(os.environ['MOCK_STATE'])
        state = json.loads(path.read_text())
        with open(os.environ['MOCK_REQUESTS'], 'a') as requests:
            requests.write(json.dumps(query) + '\n')
        args = {key: json.loads(value) for key, value in re.findall(
            r'(serviceID|environmentID|oldKey|newKey|key|value|_id):\s*("(?:[^"\\]|\\.)*")', query)}
        assert args['environmentID'] == 'production-id'
        fail = os.environ['MOCK_FAILURE']
        if query.startswith('{ service('):
            assert query.endswith('{ key } } }')
            service = args['_id']
            if fail == 'lookup-errors':
                print(json.dumps({'errors': [{'message': 'provider-private-value'}]}))
            elif fail == 'lookup-null':
                print(json.dumps({'data': {'service': {'variables': None}}}))
            elif fail == 'lookup-invalid':
                print('not-json provider-private-value')
            elif fail == 'lookup-duplicate':
                print(json.dumps({'data': {'service': {'variables': [{'key': 'X'}, {'key': 'X'}]}}}))
            elif fail == 'lookup-type':
                print(json.dumps({'data': {'service': {'variables': [{'key': 42}]}}}))
            else:
                print(json.dumps({'data': {'service': {'variables': [{'key': k} for k in state[service]]}}}))
            sys.exit(0)
        assert query.endswith('{ key } }')
        assert 'updateEnvironmentVariable(' not in query
        service = args['serviceID']
        if fail == 'mutation-errors':
            print(json.dumps({'errors': [{'message': 'provider-private-value ' + args['value']}]}))
            sys.exit(0)
        if 'updateSingleEnvironmentVariable(' in query:
            operation = 'updateSingleEnvironmentVariable'
            key = args['oldKey']
            assert key == args['newKey'] and key in state[service]
            response = [{'key': k} for k in state[service]]
        else:
            assert 'createEnvironmentVariable(' in query
            operation = 'createEnvironmentVariable'
            key = args['key']
            assert key not in state[service]
            response = {'key': key}
        if fail == 'mutation-invalid':
            print(json.dumps({'data': {operation: None}}))
            sys.exit(0)
        state[service][key] = args['value']
        path.write_text(json.dumps(state))
        print(json.dumps({'data': {operation: response}}))
        ''').lstrip())
    mock_curl.chmod(0o755)
    env = {
        **os.environ, 'PATH': str(tmp_path) + os.pathsep + os.environ['PATH'],
        'MOCK_STATE': str(state_path), 'MOCK_REQUESTS': str(requests_path), 'MOCK_FAILURE': failure,
        'ZBR_API': 'https://provider.invalid/graphql', 'ZEABUR_TOKEN': 'fixture-auth-secret',
        'EID': 'production-id', 'TG_API_ID': '123', 'TG_API_HASH': 'new-hash',
        'SB_URL': 'new-url', 'SB_SECRET': 'new-db-secret', 'API_ADMIN_TOKEN': 'new-admin-secret',
        'EXPECTED_WORKER_ID': 'open-tgate-worker-1', **(overrides or {}),
    }
    result = subprocess.run(['bash', '-c', script], cwd=tmp_path, env=env,
                            text=True, capture_output=True, check=False)
    assert requests_path.exists(), result.stderr
    requests = [json.loads(line) for line in requests_path.read_text().splitlines()]
    updated = json.loads(state_path.read_text())
    return result, state, updated, requests


def test_runtime_update_preserves_unmanaged_settings_and_updates_both_exact_services(tmp_path):
    quoted_secret = 'quoted "secret"\\\n$(echo unexpected-command)'
    result, before, after, requests = runtime_update_fixture(tmp_path, overrides={'TG_API_HASH': quoted_secret})
    assert result.returncode == 0, result.stderr
    for role in ['api-id', 'worker-id']:
        for key in ['SENTRY_DSN', 'SENTRY_ENVIRONMENT', 'OPEN_CONNECT_AI_MODEL', 'CUSTOM_SETTING']:
            assert after[role][key] == before[role][key]
        assert after[role]['TELEGRAM_API_HASH'] == quoted_secret
        assert after[role]['SUPABASE_URL'] == 'new-url'
        assert after[role]['SUPABASE_SECRET_KEY'] == 'new-db-secret'
        assert after[role]['EXTERNAL_SEND_ENABLED'] == 'false'
    assert after['unrelated-id'] == before['unrelated-id']
    assert sum(q.startswith('{ service(') for q in requests) == 2
    assert any('updateSingleEnvironmentVariable(' in q for q in requests)
    assert any('createEnvironmentVariable(' in q for q in requests)
    assert result.stdout == result.stderr == ''


def test_empty_managed_values_never_blank_existing_runtime_settings(tmp_path):
    result, before, after, requests = runtime_update_fixture(tmp_path, overrides={'TG_API_HASH': '', 'SB_URL': ''})
    assert result.returncode == 0, result.stderr
    for role in ['api-id', 'worker-id']:
        assert after[role]['TELEGRAM_API_HASH'] == before[role]['TELEGRAM_API_HASH']
        assert after[role]['SUPABASE_URL'] == before[role]['SUPABASE_URL']
    assert not any('oldKey: "TELEGRAM_API_HASH"' in q or 'oldKey: "SUPABASE_URL"' in q for q in requests)


@pytest.mark.parametrize('failure', ['lookup-errors', 'lookup-null', 'lookup-invalid', 'lookup-duplicate', 'lookup-type'])
def test_failed_runtime_name_discovery_aborts_before_any_write_and_hides_provider_output(tmp_path, failure):
    result, before, after, requests = runtime_update_fixture(tmp_path, failure=failure)
    assert result.returncode != 0
    assert after == before
    assert len(requests) == 1
    assert 'variable name discovery failed' in result.stdout
    assert 'provider-private-value' not in result.stdout + result.stderr


@pytest.mark.parametrize('failure', ['mutation-errors', 'mutation-invalid'])
def test_failed_individual_mutation_aborts_without_provider_values_or_later_service_writes(tmp_path, failure):
    result, before, after, requests = runtime_update_fixture(tmp_path, failure=failure)
    assert result.returncode != 0
    assert after == before
    assert len(requests) == 2
    assert 'variable update failed' in result.stdout
    for private in ['provider-private-value', 'new-hash', 'new-db-secret', 'new-admin-secret', 'fixture-auth-secret']:
        assert private not in result.stdout + result.stderr
