"""Marketing Numeric Guardrail enforcement.

Ensures experimental numeric targets proposed by Marketing LoRA (or base fallback)
are labeled as hypotheses/thresholds rather than established benchmarks unless supported
by project analytics, SQLite state, RAG evidence, live research, or cited benchmarks.
"""
import re

MARKETING_NUMERIC_GUARDRAIL_PROMPT = """
MARKETING NUMERIC GUARDRAIL:
The model may propose experimental numeric targets.
However:
If a numeric target is NOT supported by:
- project analytics
- SQLite state
- RAG evidence
- live research
- an explicitly cited benchmark
then it must NOT be presented as an established benchmark or factual expected performance.

Label it as:
- proposed target
- experiment threshold
- working hypothesis
or use relative improvement where appropriate (e.g. "Test for at least a +30% relative uplift versus the current baseline" or "Proposed experiment threshold: 3.5%; validate against your historical conversion data before treating this as a business benchmark").
Preserve strong experiment-design behavior without creating false precision.
"""

QUALIFICATION_MARKERS = [
    "proposed target",
    "experiment threshold",
    "working hypothesis",
    "relative uplift",
    "relative improvement",
    "validate against",
    "hypothesis",
    "benchmark:",
    "historical baseline",
    "baseline",
    "rag evidence",
    "analytics state",
]


def is_qualified(text: str) -> bool:
    t_lower = text.lower()
    return any(marker in t_lower for marker in QUALIFICATION_MARKERS)


def enforce_numeric_guardrail(text: str, has_evidence: bool = False) -> str:
    """Post-processes marketing output to guarantee numeric target guardrail compliance."""
    if not text or has_evidence:
        return text

    # 1. Transform "(Target: ...)" -> "(Proposed experiment threshold: ...; validate against historical baseline)"
    def _sub_parenthesized_target(m):
        val = m.group(1).strip()
        if is_qualified(val):
            return m.group(0)
        return f"(Proposed experiment threshold: {val}; validate against historical baseline)"

    text = re.sub(
        r'\(Target:\s*([^\)]+?)\)',
        _sub_parenthesized_target,
        text,
        flags=re.IGNORECASE
    )

    # 2. Transform standalone "Target: <value>" (not parenthesized)
    def _sub_standalone_target(m):
        prefix = m.group(1) or ""
        val = m.group(2).strip()
        if is_qualified(val):
            return m.group(0)
        return f"{prefix}Proposed experiment threshold: {val} (working hypothesis; validate against historical baseline)"

    text = re.sub(
        r'(^|\n|\.\s+)\bTarget:\s*([^\n\.;]+)',
        _sub_standalone_target,
        text,
        flags=re.IGNORECASE
    )

    # 3. Transform "Target <metric> should be / is <value>" or "<metric> target should be / is <value>"
    def _sub_target_metric(m):
        metric = (m.group(1) or m.group(2) or "").strip()
        val = m.group(3).strip()
        return f"Proposed experiment threshold for {metric}: {val} (working hypothesis; validate against historical baseline)"

    text = re.sub(
        r'(?:\bTarget\s+([a-zA-Z\s]+?)|([a-zA-Z\s]+?)\s+target)\s+(?:should\s+be|is)\s+([><=~]?\s*[\$\d\.]+%?|\$[\d,]+)',
        _sub_target_metric,
        text,
        flags=re.IGNORECASE
    )

    return text


def verify_numeric_guardrail_compliance(text: str) -> tuple[bool, str]:
    """Returns (is_compliant, explanation)."""
    t_lower = text.lower()

    # Check for unqualified targets:
    # E.g., "Target conversion rate should be 3.5%" without qualification
    m_should = re.search(r'\btarget\s+[a-z\s]+(?:should\s+be|is)\s+[\$\d\.]', t_lower)
    if m_should and "hypothesis" not in t_lower and "threshold" not in t_lower and "validate" not in t_lower:
        return False, f"Found unqualified numeric target assertion: '{m_should.group(0)}'"

    m_target = re.search(r'\(target:\s*[^\)]+\)', t_lower)
    if m_target and not is_qualified(m_target.group(0)):
        return False, f"Found unqualified (Target: ...) without hypothesis/threshold marker: '{m_target.group(0)}'"

    return True, "All numeric targets are qualified as hypotheses/thresholds or relative improvements."
