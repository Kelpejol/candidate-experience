import { StrictMode } from "react";
import { createRoot } from "react-dom/client";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { BrowserRouter } from "react-router-dom";

import App from "./App.tsx";
import "./index.css";

/**
 * One QueryClient for the whole app. React Query handles fetching, caching,
 * loading/error state, and polling (used for the async job endpoints later).
 */
const queryClient = new QueryClient({
  defaultOptions: {
    queries: {
      // Reference scaffold: keep behavior predictable and easy to reason about.
      refetchOnWindowFocus: false,
      retry: 1,
    },
  },
});

createRoot(document.getElementById("root")!).render(
  <StrictMode>
    <QueryClientProvider client={queryClient}>
      <BrowserRouter>
        <App />
      </BrowserRouter>
    </QueryClientProvider>
  </StrictMode>,
);
