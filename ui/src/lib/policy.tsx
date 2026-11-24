import { createContext, useContext, type ReactNode } from "react";

import { useHealth } from "../api/hooks";
import type { LinkPolicy } from "./safe";

// The link allow-list comes from /api/health (PUBLIC_DOMAINS, EDU_URLS). Until it
// loads, the policy is empty, so no link renders as clickable (fail closed).
const EMPTY: LinkPolicy = { publicDomains: [], eduUrls: [] };
const PolicyContext = createContext<LinkPolicy>(EMPTY);

export function PolicyProvider({ children }: { children: ReactNode }) {
  const { data } = useHealth();
  const policy: LinkPolicy = data ? { publicDomains: data.public_domains ?? [], eduUrls: data.edu_urls ?? [] } : EMPTY;
  return <PolicyContext.Provider value={policy}>{children}</PolicyContext.Provider>;
}

export function useLinkPolicy(): LinkPolicy {
  return useContext(PolicyContext);
}
