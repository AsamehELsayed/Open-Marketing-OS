"""Retrieval evidence. Project scope enforced before retrieval (fail-closed).

Mirrors rag_service.retrieve() output shape {hits, mode}. Scope filtering
must happen inside retrieval before top-k (docs/v1/rag-plan.md); cross-
project leakage is a hard eval gate (zero tolerance).
"""
from typing import Any, Literal

from pydantic import BaseModel, Field, field_validator, model_validator

RetrievalMode = Literal["FTS_ONLY", "HYBRID"]
HitSource = Literal["fts", "semantic"]
# AGENTS.md evidence discipline carried on every hit.
EvidenceStatus = Literal[
    "VERIFIED", "PARTIALLY VERIFIED", "INFERENCE", "NOT ACCESSIBLE", "UNKNOWN",
]


class RetrievalHit(BaseModel):
    path: str = Field(min_length=1)
    chunk_id: str = Field(min_length=1)
    header: str = ""
    text: str = ""
    snippet: str = ""
    file_sha: str = ""
    status_tag: str = "UNKNOWN"
    source: HitSource = "fts"
    sources: list[str] = Field(default_factory=list)
    score: float = 0.0
    project_id: str = ""
    document_id: str = ""
    file_id: str = ""
    file_name: str = ""

    @field_validator("sources")
    @classmethod
    def _known_sources(cls, v: list[str]) -> list[str]:
        for s in v:
            if s not in ("fts", "semantic"):
                raise ValueError(f"unknown hit source: {s!r}")
        return v


class RetrievalResult(BaseModel):
    query: str = Field(min_length=1)
    project_id: str = Field(min_length=1)
    mode: RetrievalMode
    hits: list[RetrievalHit] = Field(default_factory=list)
    retrieved_at: str = ""
    search_mode: str = "FTS"
    lexical_hits: int = 0
    vector_hits: int = 0
    fused_hits: int = 0
    lexical_operational: bool = False
    vector_operational: bool = False
    branch_errors: dict[str, str] = Field(default_factory=dict)

    @model_validator(mode="after")
    def _no_cross_project_hits(self) -> "RetrievalResult":
        for h in self.hits:
            if h.project_id and h.project_id != self.project_id:
                raise ValueError(
                    f"cross-project hit: {h.path} belongs to {h.project_id}")
        return self

    def hit_count(self) -> int:
        return len(self.hits)

    @classmethod
    def from_legacy(cls, query: str, project_id: str,
                    legacy: dict[str, Any]) -> "RetrievalResult":
        """Adapt rag_service.retrieve() output {hits, mode} into the contract."""
        hits = []
        for h in legacy.get("hits", []) or []:
            hits.append(RetrievalHit(
                path=h.get("path", ""), chunk_id=h.get("chunk_id", "?"),
                header=h.get("header", ""), text=h.get("text", ""),
                snippet=h.get("snippet", ""), file_sha=h.get("file_sha", ""),
                status_tag=h.get("status_tag", "UNKNOWN"),
                source=h.get("source", "fts"),
                sources=h.get("sources", [h.get("source", "fts")]),
                score=float(h.get("score", 0.0)),
                project_id=h.get("project_id", "") or project_id,
                document_id=h.get("document_id", ""), file_id=h.get("file_id", ""), file_name=h.get("file_name", ""),
            ))
        return cls(query=query, project_id=project_id,
                   mode=legacy.get("mode", "FTS_ONLY"), hits=hits,
                   **{field: legacy[field] for field in ("search_mode", "lexical_hits", "vector_hits", "fused_hits",
                       "lexical_operational", "vector_operational", "branch_errors") if field in legacy})
