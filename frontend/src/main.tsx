import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { StrictMode } from "react";
import { createRoot } from "react-dom/client";
import { BrowserRouter, Navigate, Route, Routes } from "react-router";

import { ApiError } from "./api";
import { Layout } from "./components/Layout";
import "./index.css";
import { LeaderboardPage } from "./pages/LeaderboardPage";
import { LoginPage } from "./pages/LoginPage";
import { NotFoundPage } from "./pages/NotFoundPage";
import { ReceiptPage } from "./pages/ReceiptPage";
import { ReviewQueuePage } from "./pages/ReviewQueuePage";
import { RoutingLogPage } from "./pages/RoutingLogPage";
import { UploadPage } from "./pages/UploadPage";

const queryClient = new QueryClient({
  defaultOptions: {
    queries: {
      staleTime: 30_000,
      // Only worth retrying when the server may recover; a 401 or 404 won't change.
      retry: (failures, error) =>
        failures < 2 && error instanceof ApiError && (error.status === 0 || error.status >= 500),
    },
  },
});

const root = document.getElementById("root");
if (root) {
  createRoot(root).render(
    <StrictMode>
      <QueryClientProvider client={queryClient}>
        <BrowserRouter>
          <Routes>
            <Route element={<Layout />}>
              <Route index element={<Navigate to="/leaderboard" replace />} />
              <Route path="upload" element={<UploadPage />} />
              <Route path="review" element={<ReviewQueuePage />} />
              <Route path="receipts/:digest" element={<ReceiptPage />} />
              <Route path="leaderboard" element={<LeaderboardPage />} />
              <Route path="routing-log" element={<RoutingLogPage />} />
              <Route path="login" element={<LoginPage />} />
              <Route path="*" element={<NotFoundPage />} />
            </Route>
          </Routes>
        </BrowserRouter>
      </QueryClientProvider>
    </StrictMode>,
  );
}
