"""An omitted deploy version must never silently select an obsolete image."""
import json
import os
from pathlib import Path
import re
import subprocess

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
