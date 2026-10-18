"""Public changelog releases use generic stable semantic versions."""
from pathlib import Path
import re


def test_changelog_release_headers():
    text = (Path(__file__).resolve().parents[1] / 'CHANGELOG.md').read_text()
    versions = re.findall(r'^## \[([^\]]+)\]', text, re.MULTILINE)
    assert versions
    assert all(v == 'Unreleased' or re.fullmatch(r'\d+\.\d+\.\d+', v) for v in versions)
