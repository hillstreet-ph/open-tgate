"""The runtime version must track the release version.

/readyz and worker heartbeats report ``app.__version__``, and the Zeabur deploy
gate requires it to equal the image tag. When it drifted (release-please bumped
only the manifest/changelog, never the Python module) every backend deploy
failed its readiness gate, so pin the wiring here.
"""

import json
from pathlib import Path

from app import __version__

REPO_ROOT = Path(__file__).resolve().parents[2]


def test_python_version_matches_release_manifest() -> None:
    manifest = json.loads((REPO_ROOT / ".release-please-manifest.json").read_text())
    assert __version__ == manifest["."], (
        "app.__version__ is out of sync with the release manifest; the deploy "
        "readiness gate compares the image tag to /readyz version"
    )


def test_version_line_is_annotated_for_release_please() -> None:
    source = (Path(__file__).resolve().parents[1] / "app" / "__init__.py").read_text()
    assert "x-release-please-version" in source


def test_release_please_updates_the_python_module() -> None:
    config = json.loads((REPO_ROOT / "release-please-config.json").read_text())
    extra_files = config["packages"]["."]["extra-files"]
    paths = {entry["path"] for entry in extra_files}
    assert "open-tgate/app/__init__.py" in paths
