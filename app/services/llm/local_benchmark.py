"""DEV-005 W3 — deterministic benchmark harness + manifests (no live runs).

Implements docs/v1/model-benchmark-plan.md matrix shape and
docs/v1/evaluation-plan.md determinism rule WITHOUT starting servers,
downloading models, training LoRA, or choosing a winner.

- Manifest records hardware/model/GGUF provenance (pinned weight hash +
  serving config required before any candidate enters).
- Held-out cases cover arabic_msa / egyptian / english / mixed slices.
- Metrics: TTFT, latency (p50/p95), tokens/s, RAM/VRAM, quality placeholder.
- Deterministic: same inputs + same complete_fn => identical output; an
  injected clock is the only time source (no wall-clock reads).
- Output rows match benchmark-matrix.csv columns; every report is stamped
  NOT_RUN (harness definition only — no benchmark was executed here).
"""
from dataclasses import asdict, dataclass, field

NOT_RUN_PREFIX = ("NOT_RUN: harness + manifest definition only; "
                  "no live llama.cpp server started, no benchmark executed")
LANG_SLICES = ("arabic_msa", "egyptian", "english", "mixed")
MATRIX_COLUMNS = ("model", "weight_hash", "runtime", "quant", "lang_slice",
                  "marketing_reasoning", "tool_routing", "toon_parse_rate",
                  "rag_synthesis", "citations", "hallucination_ctrl",
                  "instruction_follow", "ttft_ms", "p50_ms", "p95_ms",
                  "tok_s", "ram_gb", "vram_gb", "ctx_4k", "ctx_8k",
                  "ctx_12k", "notes")


@dataclass
class BenchmarkManifest:
    """Provenance required before a candidate may enter the matrix."""

    model: str
    weight_hash: str  # pinned, e.g. sha256:...
    runtime: str = "llama.cpp"
    runtime_version: str = ""
    quantization: str = ""
    gguf_path: str = ""
    cpu: str = ""
    ram_gb: float = 0.0
    gpu: str = "none"
    vram_gb: float = 0.0
    backend: str = "CPU"  # CPU | CUDA | ROCm | Metal | Vulkan
    server_flags: str = ""
    power_profile: str = ""
    ctx_sizes: tuple = ("4k", "8k", "12k")


@dataclass
class HeldoutCase:
    case_id: str
    lang_slice: str  # arabic_msa | egyptian | english | mixed
    prompt: str
    reference: str = ""

    def __post_init__(self) -> None:
        if self.lang_slice not in LANG_SLICES:
            raise ValueError(f"lang_slice must be one of {LANG_SLICES}")
        if not self.case_id or not self.prompt:
            raise ValueError("case_id and prompt are required")


@dataclass
class CaseResult:
    case_id: str
    lang_slice: str
    output_text: str
    ttft_ms: float
    latency_ms: float
    output_tokens: int
    ram_gb: float = 0.0
    vram_gb: float = 0.0
    quality: float = 0.0  # placeholder rubric 0..1; human rubric resolves ties


def _percentile(sorted_vals: list, pct: float) -> float:
    if not sorted_vals:
        return 0.0
    if len(sorted_vals) == 1:
        return float(sorted_vals[0])
    rank = (len(sorted_vals) - 1) * (pct / 100.0)
    lo, hi = int(rank), min(int(rank) + 1, len(sorted_vals) - 1)
    frac = rank - lo
    return float(sorted_vals[lo] + (sorted_vals[hi] - sorted_vals[lo]) * frac)


def run_benchmark(manifest: BenchmarkManifest, cases: list,
                  complete_fn, clock=None) -> dict:
    """Run the harness deterministically. complete_fn(case, manifest) -> CaseResult."""
    if not manifest.weight_hash or not manifest.gguf_path:
        raise ValueError("manifest needs pinned weight_hash + gguf_path")
    started = clock() if callable(clock) else 0.0
    results, rows = [], []
    lat_by_slice: dict = {}
    for case in cases:
        res = complete_fn(case, manifest)
        tok_per_s = (res.output_tokens / (res.latency_ms / 1000.0)
                     if res.latency_ms > 0 else 0.0)
        results.append({
            "case_id": res.case_id, "lang_slice": res.lang_slice,
            "output_text": res.output_text, "ttft_ms": res.ttft_ms,
            "latency_ms": res.latency_ms, "tok_per_s": tok_per_s,
            "output_tokens": res.output_tokens, "ram_gb": res.ram_gb,
            "vram_gb": res.vram_gb, "quality": res.quality,
        })
        lat_by_slice.setdefault(res.lang_slice, []).append(res.latency_ms)
    all_lat = sorted(r["latency_ms"] for r in results)
    summary = {
        "n_cases": len(results),
        "ttft_ms": (sum(r["ttft_ms"] for r in results) / len(results)) if results else 0.0,
        "latency_ms": (sum(r["latency_ms"] for r in results) / len(results)) if results else 0.0,
        "p50_ms": _percentile(all_lat, 50),
        "p95_ms": _percentile(all_lat, 95),
        "tok_per_s": (sum(r["tok_per_s"] for r in results) / len(results)) if results else 0.0,
        "ram_gb": max([r["ram_gb"] for r in results] + [0.0]),
        "vram_gb": max([r["vram_gb"] for r in results] + [0.0]),
        "quality": (sum(r["quality"] for r in results) / len(results)) if results else 0.0,
    }
    for r in results:
        lat = sorted(lat_by_slice.get(r["lang_slice"], [r["latency_ms"]]))
        rows.append({
            "model": manifest.model, "weight_hash": manifest.weight_hash,
            "runtime": manifest.runtime, "quant": manifest.quantization,
            "lang_slice": r["lang_slice"], "marketing_reasoning": None,
            "tool_routing": None, "toon_parse_rate": None,
            "rag_synthesis": None, "citations": None,
            "hallucination_ctrl": None, "instruction_follow": None,
            "ttft_ms": r["ttft_ms"], "p50_ms": _percentile(lat, 50),
            "p95_ms": _percentile(lat, 95), "tok_s": r["tok_per_s"],
            "ram_gb": r["ram_gb"], "vram_gb": r["vram_gb"],
            "ctx_4k": None, "ctx_8k": None, "ctx_12k": None,
            "notes": NOT_RUN_PREFIX,
        })
    ended = clock() if callable(clock) else 0.0
    _ = (started, ended)  # recorded for provenance; never wall-clock
    return {
        "manifest": asdict(manifest),
        "results": results,
        "summary": summary,
        "matrix_rows": rows,
        "matrix_columns": list(MATRIX_COLUMNS),
        "notes": NOT_RUN_PREFIX + " | winner: UNDECLARED",
        "benchmark_executed": False,
    }


def example_manifest() -> BenchmarkManifest:
    """Template provenance record — fill weight_hash/gguf/hardware on real rig."""
    return BenchmarkManifest(
        model="candidate-name", weight_hash="sha256:PINNED-BEFORE-RUN",
        runtime="llama.cpp", runtime_version="pinned-build",
        quantization="Q4_K_M", gguf_path="models/candidate-Q4_K_M.gguf",
        cpu="record-actual", ram_gb=0.0, gpu="none", vram_gb=0.0,
        backend="CPU", server_flags="--host 127.0.0.1 --ctx-size 8192")


def default_heldout_cases() -> list:
    """Held-out shape: ar + egyptian + en + mixed. Frozen before any training.

    DEV-008-PUBLISH-GATE: the prompts used to name a real business ("NJM"). The
    held-out set measures whether the model invents metrics, which is entirely
    independent of whose offer it is summarising, so the name was replaced with
    the second person. The `lang_slice` values are unaffected: "egyptian" is a
    language/dialect label, not a client identifier.
    """
    return [
        HeldoutCase(case_id="ar-1", lang_slice="arabic_msa",
                    prompt="لخص عرض شركتك في جملتين دون اختراع أرقام."),
        HeldoutCase(case_id="eg-1", lang_slice="egyptian",
                    prompt="اشرح العرض بالمصري باختصار ومن غير أرقام مخترعة."),
        HeldoutCase(case_id="en-1", lang_slice="english",
                    prompt="Summarize your offer in two sentences; invent no metrics."),
        HeldoutCase(case_id="mix-1", lang_slice="mixed",
                    prompt="Summarize العرض in two sentences, no invented metrics."),
    ]
