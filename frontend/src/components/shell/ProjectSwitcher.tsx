import { useEffect, useMemo, useState } from "react";
import { useLocation, useNavigate } from "react-router-dom";
import { api } from "../../api/client";
import Dropdown from "../chrome/Dropdown";
import { useProject } from "./project-context";

/**
 * DEV-004 W2: top-left project dropdown.
 */
export default function ProjectSwitcher() {
  const { projects, current, loading, setProjectId } = useProject();
  const navigate = useNavigate();
  const location = useLocation();
  const [businessDescribed, setBusinessDescribed] = useState<boolean | null>(null);

  useEffect(() => {
    let active = true;
    void api.getOnboardingStatus()
      .then((status) => {
        if (active) setBusinessDescribed(status.business_described);
      })
      .catch(() => {
        // Keep the normal selector available if the status endpoint is unavailable.
        if (active) setBusinessDescribed(null);
      });
    return () => { active = false; };
  }, [location.pathname]);

  const items = useMemo(() => {
    const visibleProjects = businessDescribed === true ? projects : [];
    const list = visibleProjects.map((p) => ({
      id: `project:${p.id}`,
      label: `${p.name}${current && p.id === current.id ? " ✓" : ""}`,
      onSelect: () => {},
    }));
    const setup = businessDescribed === false
      ? [{ id: "action:setup-business", label: "Set up your business…", onSelect: () => {} }]
      : [];
    return [
      ...setup,
      ...list,
      { id: "action:create", label: "+ Create project…", onSelect: () => {} },
      { id: "action:settings", label: "Project settings", onSelect: () => {} },
    ];
  }, [projects, current, businessDescribed]);

  if (loading) {
    return <div className="h-9 animate-pulse rounded-sm bg-elevated" aria-label="Loading projects" />;
  }

  const handleSelect = (id: string) => {
    if (id.startsWith("project:")) {
      const pid = id.slice("project:".length);
      if (pid !== current?.id) setProjectId(pid);
      return;
    }
    if (id === "action:setup-business") {
      navigate("/app/business/new");
      return;
    }
    if (id === "action:create") {
      navigate("/app/business/new?mode=client");
      return;
    }
    if (id === "action:settings") {
      navigate("/app/settings");
    }
  };

  return (
    <Dropdown
      label={
        <span className="inline-flex max-w-52 items-center gap-2">
          <span aria-hidden="true">▦</span>
          <span className="truncate font-semibold">
            {businessDescribed === false
              ? "Set up your business"
              : businessDescribed === null
                ? "Checking business status"
                : current ? current.name : "Select project"}
          </span>
          <span aria-hidden="true" className="text-inksecondary">▾</span>
        </span>
      }
      items={items.map((it) => ({ ...it, onSelect: handleSelect }))}
    />
  );
}
