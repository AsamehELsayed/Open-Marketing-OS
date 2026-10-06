import { Suspense, lazy, useEffect } from "react";
import { Navigate, Route, Routes, useLocation, useNavigate } from "react-router-dom";
import { api } from "./api/client";
import AppShell from "./components/chrome/AppShell";
import CommandMenu from "./components/palette/CommandMenu";
import Sidebar from "./components/shell/Sidebar";
import { ProjectProvider } from "./components/shell/project-context";

// DEV-004 W2: shell wiring only. ChatThread/Jobs/Approvals/Campaigns/
// Results/Knowledge/Settings live in W2–W5-owned routes.
// DEV-004 W4: Approvals + Jobs routes and the activity slot are live.
// DEV-004 W5: Campaigns + CampaignDetail + Results + Knowledge + Settings
// routes are live (replacing those Placeholder stubs).
// DEV-004 W4: Approvals + Jobs routes and the activity slot are live.
// ChatHome + NewChat are W2 shell surfaces.
const ChatHome = lazy(() => import("./components/shell/ChatHome"));
const NewChat = lazy(() => import("./components/shell/NewChat"));
const Chat = lazy(() => import("./routes/Chat"));
const Campaigns = lazy(() => import("./routes/Campaigns"));
const CampaignDetail = lazy(() => import("./routes/CampaignDetail"));
const Approvals = lazy(() => import("./routes/Approvals"));
const Graph = lazy(() => import("./routes/Graph"));
const Results = lazy(() => import("./routes/Results"));
const Jobs = lazy(() => import("./routes/Jobs"));
const Knowledge = lazy(() => import("./routes/Knowledge"));
const Settings = lazy(() => import("./routes/Settings"));
const StartHere = lazy(() => import("./routes/StartHere"));
const CreateBusiness = lazy(() => import("./routes/CreateBusiness"));

// DEV-004 W4: ActivityPanel owns the AppShell right slot (per W2 handoff).
// Location-aware (reads /app/chat/:id itself) so no ChatThread wiring needed.
const ActivityPanel = lazy(
  () => import("./components/activity/ActivityPanel"),
);

function Fallback() {
  return <div className="p-8 text-bodysm text-inksecondary">Loading…</div>;
}

/**
 * Keep route elements mounted across project identity updates so asynchronous
 * project selection does not reset route-local state. Project-scoped routes
 * handle identity changes within their own state boundaries. Right slot left
 * empty (AppShell default) — owned by W4.
 */
function KeyedRoutes() {
  const location = useLocation();
  const navigate = useNavigate();
  useEffect(() => {
    if (location.pathname !== "/app") return;
    let active = true;
    api.getOnboardingStatus().then((status) => {
      if (active && !status.business_described && status.intro_version_seen !== "start-here-v1") {
        navigate("/app/start", { replace: true });
      }
    }).catch(() => {
      // Keep the normal app usable if onboarding status is unavailable.
    });
    return () => { active = false; };
  }, [location.pathname, navigate]);
  return (
    <Routes>
      <Route path="/" element={<Navigate to="/app" replace />} />
      <Route path="/app" element={<ChatHome />} />
      <Route path="/app/start" element={<StartHere />} />
      <Route path="/app/business/new" element={<CreateBusiness />} />
      <Route path="/app/chat/new" element={<NewChat />} />
      <Route path="/app/chat/:id" element={<Chat />} />
      <Route path="/app/campaigns" element={<Campaigns />} />
      <Route path="/app/campaigns/:id" element={<CampaignDetail />} />
      <Route path="/app/approvals" element={<Approvals />} />
      <Route path="/app/graph" element={<Graph />} />
      <Route path="/app/results" element={<Results />} />
      <Route path="/app/jobs" element={<Jobs />} />
      <Route path="/app/knowledge" element={<Knowledge />} />
      <Route path="/app/settings" element={<Settings />} />
      <Route path="*" element={<Navigate to="/app" replace />} />
    </Routes>
  );
}

export default function App() {
  return (
    <ProjectProvider>
      <AppShell
        sidebar={<Sidebar />}
        activity={
          <Suspense fallback={<Fallback />}>
            <ActivityPanel />
          </Suspense>
        }
      >
        <CommandMenu />
        <Suspense fallback={<Fallback />}>
          <KeyedRoutes />
        </Suspense>
      </AppShell>
    </ProjectProvider>
  );
}
