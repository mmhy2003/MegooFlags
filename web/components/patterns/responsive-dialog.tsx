"use client";

import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
  DialogTrigger,
} from "@/components/ui/dialog";

/**
 * A centred dialog that is a full-screen sheet below 768 px (UI-13). The switch is CSS only,
 * so folding or rotating a device keeps the dialog open and its input intact.
 */
export function ResponsiveDialog({
  open,
  onOpenChange,
  trigger,
  title,
  description,
  children,
  footer,
  alert = false,
}: {
  open?: boolean;
  onOpenChange?: (open: boolean) => void;
  /** Omitted for a dialog that only opens through `open`. */
  trigger?: React.ReactElement;
  title: string;
  description?: React.ReactNode;
  children?: React.ReactNode;
  footer?: React.ReactNode;
  /** An alertdialog that a click outside does not dismiss. */
  alert?: boolean;
}) {
  return (
    <Dialog
      open={open}
      onOpenChange={onOpenChange}
      disablePointerDismissal={alert}
    >
      {trigger && <DialogTrigger render={trigger} />}
      <DialogContent
        // Only when set: an explicit undefined would drop Base UI's role="dialog".
        {...(alert && { role: "alertdialog" })}
        className="max-h-[calc(100dvh-2rem)] overflow-y-auto pb-[max(1rem,env(safe-area-inset-bottom))] sm:max-w-lg max-md:inset-0 max-md:top-0 max-md:left-0 max-md:h-dvh max-md:max-h-none max-md:w-screen max-md:max-w-none max-md:translate-x-0 max-md:translate-y-0 max-md:grid-rows-[auto_1fr_auto] max-md:rounded-none max-md:overflow-y-auto"
      >
        <DialogHeader className="pr-10">
          <DialogTitle>{title}</DialogTitle>
          {description && <DialogDescription>{description}</DialogDescription>}
        </DialogHeader>
        <div className="flex min-w-0 flex-col gap-3">{children}</div>
        {footer && <DialogFooter>{footer}</DialogFooter>}
      </DialogContent>
    </Dialog>
  );
}
