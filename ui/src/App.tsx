import { lazy, Suspense } from "react";
import { Navigate, Route, Routes } from "react-router-dom";

import { AppShell } from "./components/AppShell";
import { LoadingBlock } from "./components/ui";

// One chunk per route (plan P9.12).
const QueuePage = lazy(() => import("./pages/queue/QueuePage"));
const PulsePage = lazy(() => import("./pages/pulse/PulsePage"));
const SourcesPage = lazy(() => import("./pages/sources/SourcesPage"));
const ApprovalsPage = lazy(() => import("./pages/approvals/ApprovalsPage"));
const ExplainersPage = lazy(() => import("./pages/explainers/ExplainersPage"));
const NotFound = lazy(() => import("./pages/NotFound"));

export function App() {
  return (
    <AppShell>
      <Suspense fallback={<LoadingBlock rows={6} />}>
        <Routes>
          <Route path="/" element={<Navigate to="/queue" replace />} />
          <Route path="/queue" element={<QueuePage />} />
          <Route path="/queue/:ticketId" element={<QueuePage />} />
          <Route path="/pulse" element={<PulsePage />} />
          <Route path="/pulse/:runId" element={<PulsePage />} />
          <Route path="/sources" element={<SourcesPage />} />
          <Route path="/sources/drift" element={<SourcesPage />} />
          <Route path="/approvals" element={<ApprovalsPage />} />
          <Route path="/explainers" element={<ExplainersPage />} />
          <Route path="*" element={<NotFound />} />
        </Routes>
      </Suspense>
    </AppShell>
  );
}
