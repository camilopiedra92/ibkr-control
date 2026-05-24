"""Smoke test que verifica que los fixtures XML existen y son parseables."""
from pathlib import Path
from lxml import etree

FIXTURE_DIR = Path(__file__).parent / "xml"


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


def test_no_real_account_ids_in_2024():
    path = FIXTURE_DIR / "ACTIVITY_2024_sanitized.xml"
    content = path.read_text()
    assert "U99999001" not in content
    assert "U99999002" not in content
    assert "U99999003" not in content
    assert "1234567890" not in content


def test_no_real_account_ids_in_2025():
    path = FIXTURE_DIR / "ACTIVITY_2025_sanitized.xml"
    content = path.read_text()
    assert "U99999001" not in content
    assert "U99999002" not in content
    assert "U99999003" not in content


def test_empty_response_fixture():
    path = FIXTURE_DIR / "empty_query_response.xml"
    assert path.exists()
    tree = etree.parse(str(path))
    assert tree.getroot().tag == "FlexQueryResponse"
