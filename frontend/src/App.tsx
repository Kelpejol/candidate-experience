import { Navigate, Route, Routes } from "react-router-dom";

import { AppLayout } from "./components/AppLayout";
import { CallRecordsPage } from "./pages/CallRecordsPage";
import { CampaignDetailPage } from "./pages/CampaignDetailPage";
import { CampaignsPage } from "./pages/CampaignsPage";
import { HelpdeskPage } from "./pages/HelpdeskPage";

/**
 * Route table. Every screen renders inside AppLayout (shared shell/nav).
 */
export default function App() {
  return (
    <Routes>
      <Route element={<AppLayout />}>
        <Route index element={<Navigate to="/campaigns" replace />} />
        <Route path="/campaigns" element={<CampaignsPage />} />
        <Route path="/campaigns/:campaignId" element={<CampaignDetailPage />} />
        <Route path="/call-records" element={<CallRecordsPage />} />
        <Route path="/helpdesk" element={<HelpdeskPage />} />
        <Route path="*" element={<Navigate to="/campaigns" replace />} />
      </Route>
    </Routes>
  );
}
