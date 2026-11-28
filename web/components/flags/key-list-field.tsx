"use client";

import { useId, useState } from "react";

import { Label } from "@/components/ui/label";
import { Textarea } from "@/components/ui/textarea";

/** A list of context keys, one per line; applied on blur and resynced when `keys` changes. */
export function KeyListField({
  label,
  keys,
  readOnly,
  error,
  showCount = false,
  onApply,
}: {
  label: string;
  keys: string[];
  readOnly: boolean;
  error?: string;
  showCount?: boolean;
  onApply: (keys: string[]) => void;
}) {
  const id = useId();
  const joined = keys.join("\n");
  const [text, setText] = useState(joined);
  const [shown, setShown] = useState(joined);
  // Show the keys when they change from outside the field (Discard, another field).
  if (shown !== joined) {
    setShown(joined);
    setText(joined);
  }
  const countId = `${id}-count`;
  const errorId = `${id}-error`;
  const describedBy =
    [showCount && countId, error && errorId].filter(Boolean).join(" ") ||
    undefined;
  return (
    <div className="flex min-w-0 flex-col gap-2">
      <Label htmlFor={id}>{label}</Label>
      {showCount && (
        <p id={countId} className="text-sm text-muted-foreground">
          {keys.length === 1 ? "1 key" : `${keys.length} keys`}
        </p>
      )}
      <Textarea
        id={id}
        className="min-h-20 font-mono text-base"
        value={text}
        readOnly={readOnly}
        placeholder="One key per line"
        aria-invalid={error ? true : undefined}
        aria-describedby={describedBy}
        onChange={(e) => setText(e.target.value)}
        onBlur={() => onApply(text.split("\n"))}
      />
      {error && (
        <p id={errorId} className="text-sm text-destructive">
          {error}
        </p>
      )}
    </div>
  );
}
