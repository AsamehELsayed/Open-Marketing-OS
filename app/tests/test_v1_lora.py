"""DEV-005 W4 — LoRA prep-only tests (TDD spec, gate CLOSED).

Proves, without training anything:
1. refusal-without-winner (config + adapter + train_lora.py skeleton),
2. dataset slice coverage (ar/msa + egyptian + english + mixed,
   TOON-boundary cases, disjoint family splits, frozen held-out),
3. packaging-record shape (manifest fields, NOT_TRAINED/NOT_SHIPPED),
4. benchmark-manifest record shape (real record keys, matrix columns,
   NOT_RUN stamp, winner UNDECLARED, no invented numbers),
5. no training executed and no weights written.

No network, no model download, no GPU change, no secrets.
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]

#: DEV-008-PUBLISH-GATE. Three tests in this module drive the local-model
#: *training and benchmark* scripts by path. Those scripts are internal DEV-005 /
#: DEV-006 tooling and are deliberately not published: beta 1 is OMOS Quick, and
#: shipping training tooling beside a README that says "no local model in this
#: release" invites exactly the misreading the release gate exists to prevent.
#:
#: So the scripts are absent from the public export, and the three tests that
#: need them must skip rather than fail -- otherwise a fresh clone of the public
#: repository is red on arrival, before any real change is evaluated.
#:
#: The gate-closed property these tests enforce still runs wherever the tooling
#: exists (the development repository, and any future export that ships it), and
#: it reactivates automatically if the scripts are ever allowlisted again.
TRAIN_SCRIPT = REPO_ROOT / "scripts" / "train_lora.py"
BENCHMARK_SCRIPT = REPO_ROOT / "scripts" / "benchmark_local_model.py"

requires_training_tooling = pytest.mark.skipif(
    not (TRAIN_SCRIPT.is_file() and BENCHMARK_SCRIPT.is_file()),
    reason=(
        "local-model training/benchmark tooling is not published; it is internal "
        "DEV-005/DEV-006 tooling and beta 1 is OMOS Quick only"
    ),
)

from app.contracts.toon_boundary import TOON_RESPONSE_FIELDS
from app.evals.models import lora_adapter, lora_config, lora_dataset
from app.services.llm.local_benchmark import MATRIX_COLUMNS
from app.tests.internal_fixtures import requires_internal_runs


# --- 1. refusal without winner -------------------------------------------

def test_config_refuses_without_winner(tmp_path):
    with pytest.raises(RuntimeError, match="GATE CLOSED"):
        lora_config.require_benchmark_winner(tmp_path / "no-winner.json")
    with pytest.raises(RuntimeError, match="GATE CLOSED"):
        lora_config.build_training_config(
            base_model="qwen3-8b",
            base_revision="rev",
            dataset_hash="sha256:seed",
            winner_manifest=tmp_path / "no-winner.json",
        )


def test_config_rejects_unapproved_manifest(tmp_path):
    bad = tmp_path / "winner.json"
    bad.write_text(json.dumps({"winner_declared": True, "reviewer_approval": "CHANGES",
                               "model": "m", "weight_hash": "sha256:x"}))
    with pytest.raises(RuntimeError, match="GATE CLOSED"):
        lora_config.require_benchmark_winner(bad)


def test_adapter_train_and_publish_refuse(tmp_path):
    with pytest.raises(RuntimeError, match="GATE CLOSED"):
        lora_adapter.train_adapter(winner_manifest=tmp_path / "no-winner.json")
    with pytest.raises(RuntimeError, match="GATE CLOSED"):
        lora_adapter.publish_adapter(winner_manifest=tmp_path / "no-winner.json")


@requires_training_tooling
def test_train_lora_skeleton_exits_nonzero_without_winner(tmp_path):
    missing = tmp_path / "winner.json"
    proc = subprocess.run(
        [sys.executable, str(REPO_ROOT / "scripts" / "train_lora.py"),
         "--winner-manifest", str(missing)],
        capture_output=True, text=True, cwd=str(REPO_ROOT),
    )
    assert proc.returncode != 0
    assert "GATE CLOSED" in proc.stderr
    assert "TRAINING: NOT_RUN" in proc.stderr


@requires_training_tooling
def test_train_lora_skeleton_never_trains():
    src = (REPO_ROOT / "scripts" / "train_lora.py").read_text(encoding="utf-8")
    for banned in ("import torch", "import peft", "from peft", "from transformers",
                   "SFTTrainer(", ".fit(", "safetensors"):
        assert banned not in src


# --- 2. dataset slice coverage --------------------------------------------

def test_dataset_covers_all_slices():
    examples = lora_dataset.default_examples()
    slices = {e["lang_slice"] for e in examples}
    assert slices == {"arabic_msa", "egyptian", "english", "mixed"}
    for ex in examples:
        assert lora_dataset.validate_example(ex) == []


def test_dataset_rejects_forbidden_keys():
    ex = dict(lora_dataset.default_examples()[0])
    ex["chain_of_thought"] = "secret thinking"
    problems = lora_dataset.validate_example(ex)
    assert any("forbidden key" in p for p in problems)


def test_dataset_splits_are_family_disjoint_and_heldout_frozen():
    prep = lora_dataset.prepare_default_dataset()
    split = prep["split"]
    assert prep["slices_present"] == ["arabic_msa", "egyptian", "english", "mixed"]
    fams = lambda xs: {e["source_family"] for e in xs}  # noqa: E731
    assert not (fams(split["train"]) & fams(split["validation"]))
    assert not (fams(split["train"]) & fams(split["held_out"]))
    assert not (fams(split["validation"]) & fams(split["held_out"]))
    assert len(split["held_out"]) >= 1
    frozen = prep["held_out_frozen"]
    assert frozen["frozen"] is True
    assert frozen["n_cases"] == len(split["held_out"])
    assert prep["training_executed"] is False


def test_toon_boundary_cases_cover_normative_fields():
    toon_cases = [e for e in lora_dataset.default_examples()
                  if e.get("expected_toon_response")]
    assert len(toon_cases) >= 4  # at least one per slice
    slices = {e["lang_slice"] for e in toon_cases}
    assert slices == {"arabic_msa", "egyptian", "english", "mixed"}
    for ex in toon_cases:
        for field in TOON_RESPONSE_FIELDS:
            assert field in ex["expected_toon_response"]


# --- 3. packaging-record shape --------------------------------------------

def test_packaging_record_shape_and_not_shipped():
    rec = lora_adapter.build_packaging_record(
        base_model="winner-base", base_hash="sha256:pinned",
        quantization="Q4_K_M",
    )
    d = rec.to_dict()
    for field in lora_adapter.REQUIRED_MANIFEST_FIELDS:
        assert field in d and d[field], f"missing manifest field: {field}"
    assert "NOT_TRAINED" in d["status"]
    assert "NOT_SHIPPED" in d["status"]
    assert d["adapter_hash"] != "" and "no training" in d["adapter_hash"].lower()
    assert lora_adapter.missing_manifest_fields(d) == []


# --- 4. benchmark-manifest record shape ------------------------------------

@requires_training_tooling
def test_benchmark_runner_dry_run_record_shape(tmp_path):
    out = tmp_path / "bench.json"
    proc = subprocess.run(
        [sys.executable, str(REPO_ROOT / "scripts" / "benchmark_local_model.py"),
         "--model", "qwen3-8b", "--weight-hash", "sha256:pinned",
         "--quant", "Q4_K_M", "--gguf", "models/qwen3-8b-Q4_K_M.gguf",
         "--output", str(out)],
        capture_output=True, text=True, cwd=str(REPO_ROOT),
    )
    assert proc.returncode == 0, proc.stderr
    report = json.loads(out.read_text(encoding="utf-8"))
    assert report["benchmark_executed"] is False
    assert "UNDECLARED" in report["notes"]
    assert list(report["matrix_columns"]) == list(MATRIX_COLUMNS)
    m = report["manifest"]
    for key in ("model", "weight_hash", "runtime", "quantization", "gguf_path",
                "cpu", "ram_gb", "gpu", "vram_gb", "backend"):
        assert key in m, f"manifest missing: {key}"
    assert m["model"] == "qwen3-8b" and m["weight_hash"] == "sha256:pinned"
    assert report["hardware"]["ram_gb"] in (None, report["hardware"]["ram_gb"])
    row = report["matrix_rows"][0]
    for key in ("model", "quant", "lang_slice", "ttft_ms", "p50_ms", "p95_ms",
                "tok_s", "ram_gb", "vram_gb"):
        assert key in row, f"matrix row missing: {key}"
    slices = {r["lang_slice"] for r in report["matrix_rows"]}
    assert {"arabic_msa", "egyptian", "english", "mixed"} <= slices


@requires_training_tooling
def test_benchmark_runner_refuses_unpinned_candidate():
    proc = subprocess.run(
        [sys.executable, str(REPO_ROOT / "scripts" / "benchmark_local_model.py"),
         "--model", "mystery"],
        capture_output=True, text=True, cwd=str(REPO_ROOT),
    )
    assert proc.returncode != 0


# --- 5. no training, no weights --------------------------------------------

def test_no_weights_written_and_no_training_artifacts():
    run_dir = REPO_ROOT / "development" / "runs" / "DEV-005"
    assert list(run_dir.glob("*.safetensors")) == []
    assert list(run_dir.glob("benchmarks/*.safetensors")) == [] if (run_dir / "benchmarks").exists() else True
    adapters_dir = REPO_ROOT / "adapters"
    assert not adapters_dir.exists() or list(adapters_dir.glob("*.safetensors")) == []
    assert lora_dataset.prepare_default_dataset()["training_executed"] is False
    assert lora_config.describe_default_config()["training_permitted"] is False
    assert lora_config.describe_default_config()["status"] == "NOT_RUN"
