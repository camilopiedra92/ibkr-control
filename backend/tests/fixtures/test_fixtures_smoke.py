"""Smoke test que verifica que los fixtures XML existen y son parseables,
y (cuando el mapping local existe) que ningun dato real quedo sin sanitizar.

La lista de valores reales NO se hardcodea aca — vive en el archivo local
untracked ``backend/scripts/.sanitize_mapping.local.json`` (gitignored). Si el
archivo no esta (CI, clon publico), los guards anti-leak se skipean: no hay
secretos en el repo que verificar.
"""

import json
from pathlib import Path

import pytest
from lxml import etree

FIXTURE_DIR = Path(__file__).parent / "xml"
_MAPPING_PATH = Path(__file__).resolve().parents[2] / "scripts" / ".sanitize_mapping.local.json"


def _real_values() -> list[str]:
    """Claves del mapping local = los valores reales a verificar ausentes."""
    if not _MAPPING_PATH.exists():
        return []
    raw = json.loads(_MAPPING_PATH.read_text(encoding="utf-8"))
    return [k for k in raw if not k.startswith("_")]


def test_activity_2024_fixture_exists_and_parses():
    path = FIXTURE_DIR / "ACTIVITY_2024_sanitized.xml"
    assert path.exists(), f"Falta fixture: {path}"
    tree = etree.parse(str(path))
    root = tree.getroot()
    assert root.tag == "FlexQueryResponse"


def test_activity_2025_fixture_exists_and_parses():
    path = FIXTURE_DIR / "ACTIVITY_2025_sanitized.xml"
    assert path.exists()
    tree = etree.parse(str(path))
    assert tree.getroot().tag == "FlexQueryResponse"


@pytest.mark.parametrize(
    "fixture",
    [
        "ACTIVITY_2024_sanitized.xml",
        "ACTIVITY_2025_sanitized.xml",
        "ACTIVITY_2026_FOP_sanitized.xml",
    ],
)
def test_no_real_pii_in_fixtures(fixture):
    real_values = _real_values()
    if not real_values:
        pytest.skip("mapping local ausente — sin valores reales que verificar")
    content = (FIXTURE_DIR / fixture).read_text()
    leaked = [v for v in real_values if v in content]
    assert not leaked, f"PII real sin sanitizar en {fixture}: {leaked}"


def test_empty_response_fixture():
    path = FIXTURE_DIR / "empty_query_response.xml"
    assert path.exists()
    tree = etree.parse(str(path))
    assert tree.getroot().tag == "FlexQueryResponse"
