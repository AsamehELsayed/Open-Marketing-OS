"""Native PowerShell and Python timestamps must describe the same timeline."""
import importlib.util
from pathlib import Path

spec = importlib.util.spec_from_file_location(
    "dev040_execution_proof", Path(__file__).resolve().parents[2] / "scripts/dev_run.py"
)
dev_run = importlib.util.module_from_spec(spec)
spec.loader.exec_module(dev_run)


def test_native_timestamp_encodings_share_utc_timeline():
    assert dev_run.parse_ts("2026-10-19T20:56:15.9759372Z") == dev_run.parse_ts(
        "2026-10-19T23:56:15.975937+03:00"
    )
    assert dev_run.parse_ts("2026-10-19T20:37:53.711227+00:00") < dev_run.parse_ts(
        "2026-10-19T20:44:09.064030Z"
    )
    assert dev_run.parse_ts("invalid") is None
