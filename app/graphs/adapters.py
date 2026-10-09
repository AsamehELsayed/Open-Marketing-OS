"""Thin DI adapters. W1 reuses W0 contracts + existing tools/state/RAG.

No business logic is re-implemented here: every adapter delegates to
the canonical owner (repos, account_manager, rag_service,
context_router, tool registry). All entry points require an explicit
project_id and fail closed on empty scope.
"""
from app.contracts.events import safe_error_message, sanitize_metadata


def _require_scope(project_id: str) -> str:
    pid = (project_id or "").strip()
    if not pid:
        raise ValueError("NO_PROJECT_SCOPE: project_id is required (fail closed)")
    return pid


class ProjectAdapter:
    """Authoritative project read. Business source stays SQLite via repos."""

    @staticmethod
    def get_project(conn, project_id: str) -> dict:
        pid = _require_scope(project_id)
        try:
            from app.database import repos
        except Exception:
            raise ValueError("NO_PROJECT_SCOPE: project repository unavailable")
        try:
            row = repos.Projects.get(conn, pid)
        except ValueError:
            raise ValueError(f"NO_PROJECT_SCOPE: {pid} belongs elsewhere")
        if row is None:
            raise LookupError(f"NO_PROJECT_SCOPE: unknown project {pid!r}")
        return dict(row)


class ConversationAdapter:
    """Last-N messages + durable memories (top-5). Read-only."""

    @staticmethod
    def load_messages(conn, conversation_id: str, limit: int = 10) -> list:
        try:
            from app.database import repos
        except Exception:
            return []
        try:
            rows = repos.Messages.for_conversation(conn, conversation_id or "")
        except Exception:
            return []
        return [dict(r) for r in rows[-max(1, limit):]]

    @staticmethod
    def load_memories(conn, project_id: str, limit: int = 5) -> list:
        try:
            from app.database import repos
        except Exception:
            return []
        try:
            rows = repos.Memories.list(conn, _require_scope(project_id))
        except Exception:
            return []
        return [dict(r) for r in rows[:max(1, limit)]]


class ToolRegistryAdapter:
    """Injectable registry. Defaults to the legacy default registry."""

    def __init__(self, registry=None):
        if registry is not None:
            self._registry = registry
            return
        try:
            from app.services.tools import build_default_registry

            self._registry = build_default_registry()
        except Exception:
            self._registry = None

    @property
    def registry(self):
        return self._registry

    def names(self) -> list:
        if self._registry is None:
            return []
        try:
            return list(self._registry.names())
        except Exception:
            return []

    def execute(self, conn, *, project_id: str, root, name: str, args: dict,
                execution_context: dict | None = None) -> dict:
        try:
            _require_scope(project_id)
        except ValueError as e:
            return {
                "ok": False,
                "error": safe_error_message(e, "The project scope is unavailable."),
                "status": "failed",
            }
        if self._registry is None:
            return {"ok": False, "error": "tool registry unavailable", "status": "failed"}
        execute_kwargs = {
            "project_id": project_id, "root": root, "name": name,
            "args": args or {},
        }
        if execution_context is not None:
            execute_kwargs["execution_context"] = execution_context
        result = self._registry.execute(conn, **execute_kwargs)
        if not isinstance(result, dict):
            return {"ok": False, "error": "The tool could not be completed.", "status": "failed"}
        projected = sanitize_metadata(result)
        if "error" in result:
            projected["error"] = safe_error_message(
                result.get("error"),
                "The tool could not be completed.",
                force_generic=True,
            )
        return projected


class RetrievalAdapter:
    """Scope-first retrieval: project_id enforced before top-k.

    Delegates to retrieve_scoped, then adapts via the W0
    RetrievalResult contract (which rejects cross-project hits).
    """

    def __init__(self, conn, project_id: str, store=None, provider=None):
        self._conn = conn
        self._project_id = _require_scope(project_id)
        self._store = store
        self._provider = provider

    def retrieve(self, query: str):
        from app.contracts.retrieval import RetrievalResult

        pid = _require_scope(self._project_id)
        if not (query or "").strip():
            return RetrievalResult(query=query or "", project_id=pid,
                                   mode="FTS_ONLY", hits=[])
        root = None
        if self._store is None or self._provider is None:
            try:
                from app.paths import user_data_root
                root = user_data_root()
                from app.services.knowledge_index import retrieval_runtime
                self._store, self._provider = retrieval_runtime(root, pid)
            except Exception:
                # No vector runtime is still a valid FTS-only retrieval path.
                self._store, self._provider = None, None
        try:
            from app.services.rag.scoped_retrieval import retrieve_scoped
            legacy = retrieve_scoped(
                self._conn, query, project_id=pid, store=self._store,
                provider=self._provider, mode="hybrid")
        except Exception:
            # Fail closed on a broken scoped/vector path; lexical scoped
            # retrieval remains available and never falls back to global top-k.
            from app.services.rag.scoped_retrieval import retrieve_scoped
            legacy = retrieve_scoped(self._conn, query, project_id=pid,
                                     store=None, provider=None, mode="lexical")
        return RetrievalResult.from_legacy(query, pid, legacy or {})


class WebAdapter:
    """Evidence-only web search through the tool registry (injectable)."""

    def __init__(self, tools: ToolRegistryAdapter | None = None):
        self._tools = tools or ToolRegistryAdapter()

    def search(self, conn, *, project_id: str, root, query: str, count: int = 5) -> dict:
        pid = _require_scope(project_id)
        return self._tools.execute(conn, project_id=pid, root=root,
                                   name="web_search",
                                   args={"query": query, "count": count})


class SocialAdapter:
    """Provider-first social reads via existing instagram helpers."""

    @staticmethod
    def detect_intent(text: str):
        try:
            from app.services.tools import instagram_tools as ig

            return ig.detect_instagram_intent(text or "")
        except Exception:
            lowered = (text or "").lower()
            if "instagram" in lowered or "@" in (text or ""):
                return {"intent": "instagram_generic"}
            return None

    @staticmethod
    def fallback_reply(conn, project_id: str, text: str, lang: str):
        try:
            from app.services import account_manager as legacy

            return legacy._instagram_fallback_reply(conn, project_id, text, lang)
        except Exception:
            return None
