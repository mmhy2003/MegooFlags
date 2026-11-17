"use client";

import { useId, useState } from "react";
import { toast } from "sonner";

import { NodeToken, TOKEN_WARNING } from "@/components/nodes/node-token";
import { ResponsiveDialog } from "@/components/patterns/responsive-dialog";
import { Button } from "@/components/ui/button";
import { DialogClose } from "@/components/ui/dialog";
import { Label } from "@/components/ui/label";
import { NativeSelect } from "@/components/ui/native-select";
import { api } from "@/lib/api/client";
import type { components } from "@/lib/api/schema";
import { must } from "@/lib/flags/errors";
import { DEFAULT_OVERLAP, OVERLAPS } from "@/lib/nodes/form";

type Node = components["schemas"]["NodeOut"];

function Form({
  node,
  onRotated,
  onError,
}: {
  node: Node;
  onRotated: (token: string) => void;
  onError: (error: unknown) => void;
}) {
  const id = useId();
  const [overlap, setOverlap] = useState(DEFAULT_OVERLAP);
  const [busy, setBusy] = useState(false);

  const submit = async (e: React.FormEvent) => {
    e.preventDefault();
    setBusy(true);
    try {
      const rotated = await must(
        api.POST("/api/nodes/{id}/rotate", {
          params: { path: { id: node.id } },
          body: { overlap_seconds: overlap },
        }),
      );
      toast("Token rotated");
      onRotated(rotated.token);
    } catch (error) {
      setBusy(false);
      onError(error);
    }
  };

  return (
    <form noValidate onSubmit={submit} className="flex flex-col gap-5">
      <div className="flex min-w-0 flex-col gap-2">
        <Label htmlFor={`${id}-overlap`}>Old token keeps working for</Label>
        <NativeSelect
          id={`${id}-overlap`}
          value={String(overlap)}
          onChange={(e) => setOverlap(Number(e.target.value))}
        >
          {OVERLAPS.map((o) => (
            <option key={o.seconds} value={o.seconds}>
              {o.label}
            </option>
          ))}
        </NativeSelect>
      </div>
      <div className="flex flex-col-reverse gap-2 sm:flex-row sm:justify-end">
        <DialogClose render={<Button type="button" variant="outline" />}>
          Cancel
        </DialogClose>
        <Button type="submit" disabled={busy}>
          Rotate token
        </Button>
      </div>
    </form>
  );
}

/** Gives `node` a new token, then shows it and its `.env` lines once. */
export function RotateNodeDialog({
  node,
  open,
  onOpenChange,
  onRotated,
  onError,
}: {
  /** Stays set while the dialog closes, so its title doesn't change mid-animation. */
  node: Node | null;
  open: boolean;
  onOpenChange: (open: boolean) => void;
  /** After the API rotated the token, while the new one is still on screen. */
  onRotated: () => void;
  onError: (error: unknown) => void;
}) {
  const [token, setToken] = useState<string | null>(null);
  const change = (next: boolean) => {
    // The token is gone for good once the dialog closes.
    if (!next) setToken(null);
    onOpenChange(next);
  };
  return (
    <ResponsiveDialog
      // Once the token shows, a stray click outside must not throw it away.
      alert={token !== null}
      open={open}
      onOpenChange={change}
      title={`Rotate the token of ${node?.name ?? "the worker"}`}
      description={
        token === null
          ? "The worker needs the new token in MEGOOFLAGS_NODE_TOKEN before the old one stops working."
          : TOKEN_WARNING
      }
      footer={
        token !== null && <Button onClick={() => change(false)}>Done</Button>
      }
    >
      {open &&
        node &&
        (token === null ? (
          <Form
            key={node.id}
            node={node}
            onRotated={(t) => {
              setToken(t);
              onRotated();
            }}
            onError={onError}
          />
        ) : (
          <NodeToken token={token} />
        ))}
    </ResponsiveDialog>
  );
}
