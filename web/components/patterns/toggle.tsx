"use client";

import { Switch } from "@/components/ui/switch";

/** A switch with a 44 px hit area and its state in words, not colour alone. */
export function Toggle({
  checked,
  onCheckedChange,
  label,
  disabled,
  showState = true,
}: {
  checked: boolean;
  onCheckedChange: (checked: boolean) => void;
  label: string;
  disabled?: boolean;
  showState?: boolean;
}) {
  return (
    <label
      data-hit-area
      className="inline-flex min-h-11 min-w-11 items-center gap-2"
    >
      <Switch
        checked={checked}
        onCheckedChange={(value) => onCheckedChange(value)}
        aria-label={label}
        disabled={disabled}
      />
      {showState && (
        <span className="text-sm font-medium">{checked ? "On" : "Off"}</span>
      )}
    </label>
  );
}
