from app.tests.internal_fixtures import (
    INTERNAL_RUN_FIXTURE_PATHS,
    internal_run_fixtures_available,
)


def test_dev031_run_directory_alone_does_not_enable_legacy_fixture_tests(tmp_path):
    (tmp_path / "DEV-031").mkdir()
    assert not internal_run_fixtures_available(tmp_path)

    for relative_path in INTERNAL_RUN_FIXTURE_PATHS:
        artifact = tmp_path / relative_path
        artifact.parent.mkdir(parents=True, exist_ok=True)
        artifact.write_text("fixture", encoding="utf-8")

    assert internal_run_fixtures_available(tmp_path)
