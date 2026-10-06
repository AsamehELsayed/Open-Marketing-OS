from pathlib import Path

from scripts.package.secret_scan import scan


def test_only_the_confirmed_event_catalog_pem_literal_is_exempt(tmp_path: Path):
    allowed_value = "-----BEGIN " + "RSA PRIVATE KEY-----"
    other_value = "-----BEGIN " + "EC PRIVATE KEY-----"
    allowed = tmp_path / "app/tests/test_dev008so_event_catalog.py"
    allowed.parent.mkdir(parents=True)
    allowed.write_text(f'"{allowed_value}"\n', encoding="utf-8")

    different_fixture = tmp_path / "app/tests/test_other_pem_fixture.py"
    different_fixture.write_text(f'"{other_value}"\n', encoding="utf-8")

    findings = scan(tmp_path)["findings"]
    assert [(hit["file"], hit["id"]) for hit in findings] == [
        ("app/tests/test_other_pem_fixture.py", "private_key_block")
    ]


def test_private_key_detector_remains_enabled_for_unrelated_pem_values(tmp_path: Path):
    unrelated_value = "-----BEGIN " + "OPENSSH PRIVATE KEY-----"
    unrelated = tmp_path / "docs/example.txt"
    unrelated.parent.mkdir(parents=True)
    unrelated.write_text(unrelated_value + "\n", encoding="utf-8")

    findings = scan(tmp_path)["findings"]
    assert len(findings) == 1
    assert findings[0]["id"] == "private_key_block"
    assert findings[0]["file"] == "docs/example.txt"
