import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";

import { api, type TicketFilters } from "./client";
import type { RefineKind } from "./types";

export const keys = {
  health: ["health"] as const,
  tickets: (filters: TicketFilters) => ["tickets", filters] as const,
  ticket: (id: string) => ["ticket", id] as const,
  runs: ["weekly", "runs"] as const,
  run: (id: string) => ["weekly", "run", id] as const,
  sources: ["sources"] as const,
  actions: (status: string) => ["actions", status] as const,
  explainers: ["explainers"] as const,
};

export const useHealth = () => useQuery({ queryKey: keys.health, queryFn: api.health, refetchInterval: 30_000 });
export const useTickets = (filters: TicketFilters) =>
  useQuery({ queryKey: keys.tickets(filters), queryFn: () => api.tickets(filters), refetchInterval: 15_000 });
export const useTicket = (id: string | undefined) =>
  useQuery({ queryKey: keys.ticket(id ?? ""), queryFn: () => api.ticket(id ?? ""), enabled: Boolean(id) });
export const useRuns = () => useQuery({ queryKey: keys.runs, queryFn: api.weeklyRuns });
export const useRun = (id: string | undefined) =>
  useQuery({ queryKey: keys.run(id ?? ""), queryFn: () => api.weeklyRun(id ?? ""), enabled: Boolean(id) });
export const useSources = () => useQuery({ queryKey: keys.sources, queryFn: api.sources, staleTime: 5 * 60_000 });
export const useActions = (status: string) =>
  useQuery({ queryKey: keys.actions(status), queryFn: () => api.actions(status) });
export const useExplainers = () => useQuery({ queryKey: keys.explainers, queryFn: api.canonicalExplainers });

/**
 * Ticket mutations replace that ticket's cache entry and refresh the lists. No other
 * ticket's detail is touched (plan P9.5).
 */
function useTicketMutation<A>(fn: (id: string, arg: A) => Promise<unknown>) {
  const client = useQueryClient();
  return useMutation({
    mutationFn: ({ id, arg }: { id: string; arg: A }) => fn(id, arg),
    onSuccess: (data, { id }) => {
      client.setQueryData(keys.ticket(id), data);
      void client.invalidateQueries({ queryKey: ["tickets"] });
      void client.invalidateQueries({ queryKey: ["actions"] });
      void client.invalidateQueries({ queryKey: keys.health });
    },
  });
}

export const useEditTicket = () => useTicketMutation<string>((id, text) => api.editTicket(id, text));
export const useRejectTicket = () => useTicketMutation<void>((id) => api.rejectTicket(id));
export const useRefineTicket = () =>
  useTicketMutation<{ kind: RefineKind; hint: string }>((id, { kind, hint }) => api.refineTicket(id, kind, hint));

export function useCreateTicket() {
  const client = useQueryClient();
  return useMutation({
    mutationFn: api.createTicket,
    onSuccess: () => {
      void client.invalidateQueries({ queryKey: ["tickets"] });
      void client.invalidateQueries({ queryKey: ["actions"] });
      void client.invalidateQueries({ queryKey: keys.health });
    },
  });
}

export function useSeedTickets() {
  const client = useQueryClient();
  return useMutation({
    mutationFn: api.seedTickets,
    onSuccess: () => void client.invalidateQueries({ queryKey: ["tickets"] }),
  });
}

export function useRunWeekly() {
  const client = useQueryClient();
  return useMutation({
    mutationFn: api.runWeekly,
    onSuccess: () => {
      void client.invalidateQueries({ queryKey: ["weekly"] });
      void client.invalidateQueries({ queryKey: ["actions"] });
      void client.invalidateQueries({ queryKey: keys.health });
    },
  });
}

/**
 * Approve or reject exactly one action. Only the action lists, the subject it belongs
 * to and the header count refresh; other cards keep their state (plan P9.9, EDIT-02).
 */
export function useDecideAction(decision: "approve" | "reject") {
  const client = useQueryClient();
  return useMutation({
    mutationFn: (actionId: string) => (decision === "approve" ? api.approveAction(actionId) : api.rejectAction(actionId)),
    onSuccess: (action) => {
      void client.invalidateQueries({ queryKey: ["actions"] });
      void client.invalidateQueries({ queryKey: keys.health });
      if (action.subject_type === "ticket") {
        void client.invalidateQueries({ queryKey: keys.ticket(action.subject_id) });
        void client.invalidateQueries({ queryKey: ["tickets"] });
      } else {
        void client.invalidateQueries({ queryKey: keys.explainers });
      }
    },
  });
}
