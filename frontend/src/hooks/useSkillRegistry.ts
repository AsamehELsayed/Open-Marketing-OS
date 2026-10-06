import { useCallback, useEffect, useState } from "react";
import { api, type SpaSkillsResponse } from "../api/client";

/**
 * DEV-008-SKILLS-OPS — the marketing playbook registry, as the UI sees it.
 *
 * A **skill** is a vendored `SKILL.md` from the pinned upstream library: it is
 * knowledge, not capability. It has no parameters, no handler, no permission
 * level and no side effect. This hook therefore reads and toggles playbooks and
 * nothing else — it never registers a tool and never performs an action.
 *
 * HONESTY RULES THIS HOOK KEEPS
 * - The library size is **data**. `total`, `valid`, `missing`, `invalid` and
 *   `file_count` come from the server's measured loader run. There is no
 *   expected-count constant anywhere in this file, so a re-pin of the pin
 *   needs no frontend change and the tab can never claim a number it did not
 *   receive.
 * - `library_root` is a RELATIVE label by construction. An absolute path would
 *   be redacted to `[REDACTED]` in transit and would look populated while being
 *   useless, so the server ships the label and the UI shows the label.
 * - A load failure is surfaced as an error string. It is never rendered as an
 *   empty library, because "0 skills" and "could not reach the registry" are
 *   different facts and conflating them is how a broken install looks healthy.
 * - `missing` / `invalid` are shown even when zero, because a registry that
 *   silently drops a playbook is the defect this run exists to remove.
 */

export interface SkillRegistryState {
  data: SpaSkillsResponse | null;
  loading: boolean;
  error: string | null;
  /** Skill id currently being toggled, or null. */
  savingSkillId: string | null;
  reload: () => void;
  setEnabled: (skillId: string, enabled: boolean) => Promise<void>;
}

function message(err: unknown, fallback: string): string {
  if (err instanceof Error && err.message) return err.message;
  return fallback;
}

export function useSkillRegistry(): SkillRegistryState {
  const [data, setData] = useState<SpaSkillsResponse | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [savingSkillId, setSavingSkillId] = useState<string | null>(null);
  const [nonce, setNonce] = useState(0);

  const reload = useCallback(() => {
    setNonce((n) => n + 1);
  }, []);

  useEffect(() => {
    let cancelled = false;
    setLoading(true);
    setError(null);
    api
      .getSkills()
      .then((res) => {
        if (cancelled) return;
        setData(res);
        setLoading(false);
      })
      .catch((err: unknown) => {
        if (cancelled) return;
        setData(null);
        setError(message(err, "Could not load the skills registry."));
        setLoading(false);
      });
    return () => {
      cancelled = true;
    };
  }, [nonce]);

  /**
   * Toggle one playbook, then re-read the registry.
   *
   * The response is NOT trusted as the new state: the server recomputes
   * `enabled` per request from the stored setting, so a re-read is the only
   * way the UI shows what the router will actually see. Optimistically flipping
   * the row would be a claim the backend has not confirmed yet.
   */
  const setEnabled = useCallback(
    async (skillId: string, enabled: boolean) => {
      setSavingSkillId(skillId);
      setError(null);
      try {
        await api.setSkillEnabled(skillId, enabled);
        const res = await api.getSkills();
        setData(res);
      } catch (err: unknown) {
        setError(message(err, `Could not update ${skillId}.`));
      } finally {
        setSavingSkillId(null);
      }
    },
    [],
  );

  return { data, loading, error, savingSkillId, reload, setEnabled };
}
