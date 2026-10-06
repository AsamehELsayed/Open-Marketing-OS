import {
  createContext,
  useCallback,
  useContext,
  useEffect,
  useMemo,
  useRef,
  useState,
  type ReactNode,
} from "react";
import { api, type SpaProject } from "../../api/client";

const STORAGE_KEY = "omos.projectId";

interface ProjectState {
  projects: SpaProject[];
  projectId: string | null;
  current: SpaProject | null;
  loading: boolean;
  error: string | null;
  hasExplicitProjectSelection: boolean;
  setProjectId: (id: string | null) => void;
  setProjectIdFromConversation: (id: string) => void;
  refresh: () => void;
}

const ProjectCtx = createContext<ProjectState>({
  projects: [],
  projectId: null,
  current: null,
  loading: true,
  error: null,
  hasExplicitProjectSelection: false,
  setProjectId: () => {},
  setProjectIdFromConversation: () => {},
  refresh: () => {},
});

/**
 * DEV-004 W2: single source of truth for the active project.
 * Persists to localStorage; defaults to the stored id when still valid,
 * otherwise the first project. Null = no-project state.
 */
export function ProjectProvider({ children }: { children: ReactNode }) {
  const [projects, setProjects] = useState<SpaProject[]>([]);
  const [projectId, setProjectIdRaw] = useState<string | null>(() => {
    try {
      return window.localStorage.getItem(STORAGE_KEY);
    } catch {
      return null;
    }
  });
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [hasExplicitProjectSelection, setHasExplicitProjectSelection] = useState(false);
  const selectionChangedRef = useRef(false);
  const [nonce, setNonce] = useState(0);

  useEffect(() => {
    let cancelled = false;
    setLoading(true);
    setError(null);
    api
      .getProjects()
      .then((list) => {
        if (cancelled) return;
        setProjects(list);
        let storedId: string | null = null;
        try {
          storedId = window.localStorage.getItem(STORAGE_KEY);
        } catch {
          // Storage may be unavailable; the selection can remain in memory.
        }
        const storedSelectionIsValid = Boolean(storedId && list.some((p) => p.id === storedId));
        if (!selectionChangedRef.current) {
          setHasExplicitProjectSelection(storedSelectionIsValid);
          if (storedId && !storedSelectionIsValid) {
            try {
              window.localStorage.removeItem(STORAGE_KEY);
            } catch {
              // Storage unavailable; the in-memory fallback still applies.
            }
          }
        }
        setProjectIdRaw((prev) => {
          if (prev && list.some((p) => p.id === prev)) return prev;
          return list.length > 0 ? list[0].id : null;
        });
        setLoading(false);
      })
      .catch((e: unknown) => {
        if (cancelled) return;
        setError(e instanceof Error ? e.message : "failed to load projects");
        setLoading(false);
      });
    return () => {
      cancelled = true;
    };
  }, [nonce]);

  const setProjectId = useCallback((id: string | null) => {
    selectionChangedRef.current = true;
    setHasExplicitProjectSelection(Boolean(id));
    setProjectIdRaw(id);
    try {
      if (id) window.localStorage.setItem(STORAGE_KEY, id);
      else window.localStorage.removeItem(STORAGE_KEY);
    } catch {
      /* storage unavailable — in-memory only */
    }
  }, []);

  // Deep-link alignment is an in-memory route correction, not an explicit
  // user selection, so it must not turn the automatic project into storage.
  const setProjectIdFromConversation = useCallback((id: string) => {
    setProjectIdRaw(id);
  }, []);

  const refresh = useCallback(() => setNonce((n) => n + 1), []);

  const value = useMemo<ProjectState>(() => {
    const current = projects.find((p) => p.id === projectId) ?? null;
    return {
      projects,
      projectId: current ? current.id : projectId,
      current,
      loading,
      error,
      hasExplicitProjectSelection,
      setProjectId,
      setProjectIdFromConversation,
      refresh,
    };
  }, [projects, projectId, loading, error, hasExplicitProjectSelection, setProjectId, setProjectIdFromConversation, refresh]);

  return <ProjectCtx.Provider value={value}>{children}</ProjectCtx.Provider>;
}

export function useProject(): ProjectState {
  return useContext(ProjectCtx);
}
