import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { StrictMode } from "react";
import { createRoot } from "react-dom/client";
import App from "./App.tsx";
import { ErrorBoundary } from "./desktop/ErrorBoundary";
import "./index.css";
import "./viewer/viewerStyles.css";
import "./fonts.css";
import "@fontsource-variable/geist";
import "@fontsource-variable/geist-mono";

const queryClient = new QueryClient({
  defaultOptions: { queries: { retry: 1, refetchOnWindowFocus: false } },
});

const root = document.getElementById("root");
if (!root) {
  throw new Error("missing #root");
}

createRoot(root).render(
  <StrictMode>
    <QueryClientProvider client={queryClient}>
      <ErrorBoundary>
        <App />
      </ErrorBoundary>
    </QueryClientProvider>
  </StrictMode>,
);
