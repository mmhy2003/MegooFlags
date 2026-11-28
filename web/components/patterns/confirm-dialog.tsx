"use client";

import { useState } from "react";

import { ResponsiveDialog } from "@/components/patterns/responsive-dialog";
import { Button } from "@/components/ui/button";
import { DialogClose } from "@/components/ui/dialog";

/**
 * An alertdialog that asks before an action runs; Cancel leaves everything as it was. Pass
 * `open` and `onOpenChange` instead of `trigger` to open it from somewhere that unmounts, such
 * as a menu item.
 */
export function ConfirmDialog({
  trigger,
  open: controlledOpen,
  onOpenChange,
  title,
  description,
  confirmLabel,
  destructive = false,
  onConfirm,
}: {
  trigger?: React.ReactElement;
  open?: boolean;
  onOpenChange?: (open: boolean) => void;
  title: string;
  description: React.ReactNode;
  confirmLabel: string;
  destructive?: boolean;
  onConfirm: () => void;
}) {
  const [ownOpen, setOwnOpen] = useState(false);
  const open = controlledOpen ?? ownOpen;
  const setOpen = (next: boolean) => {
    setOwnOpen(next);
    onOpenChange?.(next);
  };
  return (
    <ResponsiveDialog
      alert
      open={open}
      onOpenChange={setOpen}
      trigger={trigger}
      title={title}
      description={description}
      footer={
        <>
          <DialogClose render={<Button variant="outline" />}>
            Cancel
          </DialogClose>
          <Button
            variant={destructive ? "destructive" : "default"}
            onClick={() => {
              onConfirm();
              setOpen(false);
            }}
          >
            {confirmLabel}
          </Button>
        </>
      }
    />
  );
}
