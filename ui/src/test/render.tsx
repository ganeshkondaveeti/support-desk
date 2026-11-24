import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render } from "@testing-library/react";
import type { ReactElement } from "react";
import { MemoryRouter, Route, Routes } from "react-router-dom";

import { PolicyProvider } from "../lib/policy";

/** Render with a fresh query client, router and link policy. */
export function renderWithApp(ui: ReactElement, { route = "/", path = "*" }: { route?: string; path?: string } = {}) {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false, staleTime: Infinity }, mutations: { retry: false } } });
  return render(
    <QueryClientProvider client={client}>
      <MemoryRouter initialEntries={[route]}>
        <PolicyProvider>
          <Routes>
            <Route path={path} element={ui} />
          </Routes>
        </PolicyProvider>
      </MemoryRouter>
    </QueryClientProvider>,
  );
}
