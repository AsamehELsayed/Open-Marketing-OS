"""Knowledge tools (v0.2 §6.2). RAG evidence only — never returned as final answer."""
from app.services.rag import context_router, rag_service


def _get_store(root, project_id: str | None = None):
    try:
        from app.services.rag.chroma_store import ChromaStore
        from app.services.rag.embeddings import LocalHashEmbeddings
        from pathlib import Path
        provider = LocalHashEmbeddings()
        return ChromaStore(Path(root) / "data" / "chroma", provider, project_id=project_id), provider
    except Exception:
        return None, None


def t_rag_search(conn, *, project_id, root, args):
    query = (args.get("query") or "").strip()
    if not query:
        return {"ok": False, "error": "query is required", "status": "failed"}
    try:
        k = max(1, min(10, int(args.get("k", 6))))
    except (ValueError, TypeError):
        k = 6
    store, provider = _get_store(root, project_id)
    res = rag_service.retrieve(conn, query, store, provider, project_id=project_id)
    state_block = {"campaigns": [], "approvals": []}
    try:
        from app.database import repos
        state_block = {"campaigns": repos.Campaigns.list(conn, project_id),
                       "approvals": repos.Approvals.list(conn, project_id=project_id)}
    except Exception:
        pass
    ctx = context_router.build_context(conn, root, query, state_block, res.get("hits", []))
    hits = [{"path": h["path"], "chunk_id": h["chunk_id"], "header": h.get("header", ""),
             "snippet": h.get("snippet", "")[:500], "score": round(float(h.get("score", 0)), 4),
             "status_tag": h.get("status_tag", "UNKNOWN")}
            for h in res.get("hits", [])[:k]]
    return {"ok": True, "hits": hits, "mode": res.get("mode", "FTS_ONLY"),
            "enrichment": len(ctx["enrichment"]), "unknown": len(ctx["unknown"]),
            "stale": len(ctx["stale"]),
            "provenance": ctx["provenance"][:k]}


def t_search_project_knowledge(conn, *, project_id, root, args):
    base = t_rag_search(conn, project_id=project_id, root=root, args=args)
    if not base.get("ok"):
        return base
    from app.database import repos
    base["state_pointer"] = {"campaigns": len(repos.Campaigns.list(conn, project_id)),
                             "approvals_pending": len(repos.Approvals.list(conn, "pending", project_id))}
    return base


def register_knowledge_tools(registry):
    from .registry import ToolDef
    registry.register(ToolDef(name="rag_search",
                              description="Search internal project knowledge (evidence only; synthesize, never paste).",
                              parameters={"type": "object", "required": ["query"],
                                          "properties": {"query": {"type": "string"}, "k": {"type": "integer"}}},
                              side_effect="green", handler=t_rag_search))
    registry.register(ToolDef(name="search_project_knowledge",
                              description="rag_search plus a state pointer; convenience for broad questions.",
                              parameters={"type": "object", "required": ["query"],
                                          "properties": {"query": {"type": "string"}}},
                              side_effect="green", handler=t_search_project_knowledge))
