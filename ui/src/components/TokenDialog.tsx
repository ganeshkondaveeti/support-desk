import * as Dialog from "@radix-ui/react-dialog";
import { KeyRound } from "lucide-react";
import { useState, useSyncExternalStore, type FormEvent } from "react";

import { dismissTokenRequest, isTokenRequested, setToken, subscribe } from "../api/auth";
import { Button } from "./ui";

/** Asks for the reviewer token after a 401 (arch §12). Stored for this tab only. */
export function TokenDialog() {
  const open = useSyncExternalStore(subscribe, isTokenRequested);
  const [value, setValue] = useState("");
  const submit = (event: FormEvent) => {
    event.preventDefault();
    if (!value.trim()) return;
    setToken(value.trim());
    setValue("");
    dismissTokenRequest();
  };
  return (
    <Dialog.Root open={open} onOpenChange={(next) => (next ? undefined : dismissTokenRequest())}>
      <Dialog.Portal>
        <Dialog.Overlay className="fixed inset-0 z-50 bg-ink/30" />
        <Dialog.Content className="fixed left-1/2 top-1/2 z-50 w-[calc(100vw-2rem)] max-w-sm -translate-x-1/2 -translate-y-1/2 rounded-md border border-line bg-surface p-5 shadow-2">
          <form onSubmit={submit}>
            <div className="flex items-center gap-2">
              <KeyRound aria-hidden className="h-5 w-5 text-primary" />
              <Dialog.Title className="text-headline-sm">Reviewer token</Dialog.Title>
            </div>
            <Dialog.Description className="mt-1 text-body-md text-ink-muted">
              Approving, editing and running actions need the reviewer token. It is kept for this browser tab only.
            </Dialog.Description>
            <label htmlFor="reviewer-token" className="mt-4 block text-label-lg text-ink-muted">
              Token
            </label>
            <input
              id="reviewer-token"
              type="password"
              autoComplete="off"
              value={value}
              onChange={(event) => setValue(event.target.value)}
              className="mt-1 h-9 w-full rounded border border-line-strong px-2.5 text-body-md focus:border-primary focus:outline-none focus:ring-2 focus:ring-primary/20"
            />
            <div className="mt-4 flex justify-end gap-2">
              <Dialog.Close asChild>
                <Button>Cancel</Button>
              </Dialog.Close>
              <Button type="submit" variant="primary" disabled={!value.trim()}>
                Save token
              </Button>
            </div>
            <p className="mt-3 text-body-sm text-ink-faint">Then repeat the action.</p>
          </form>
        </Dialog.Content>
      </Dialog.Portal>
    </Dialog.Root>
  );
}
