"use client";

import { useState } from "react";

import { Input } from "@/components/ui/input";
import { Slider } from "@/components/ui/slider";
import { clampRollout, parseRollout } from "@/lib/rollout";

/**
 * A 0–100 % rollout in 0.1 steps: a touch-sized slider plus a number input
 * for exact values (UI-16). The input commits on blur or Enter.
 */
export function RolloutSlider({
  value,
  onValueChange,
  label,
  disabled = false,
}: {
  value: number;
  onValueChange: (value: number) => void;
  label: string;
  disabled?: boolean;
}) {
  const [draft, setDraft] = useState(String(value));
  const [shown, setShown] = useState(value);
  // Show the new value when it changes from outside the input (the slider).
  if (shown !== value) {
    setShown(value);
    setDraft(String(value));
  }

  function commit() {
    const parsed = parseRollout(draft);
    if (parsed === null) {
      setDraft(String(value));
      return;
    }
    setDraft(String(parsed));
    if (parsed !== value) onValueChange(parsed);
  }

  return (
    <div className="flex flex-wrap items-center gap-3">
      <Slider
        data-hit-area
        className="h-11 min-w-40 flex-1"
        thumbClassName="size-6"
        min={0}
        max={100}
        step={0.1}
        value={[value]}
        disabled={disabled}
        onValueChange={(v) =>
          onValueChange(clampRollout(Array.isArray(v) ? v[0] : v))
        }
        aria-label={label}
      />
      <div className="flex items-center gap-1">
        <Input
          type="number"
          inputMode="decimal"
          step={0.1}
          min={0}
          max={100}
          aria-label="Rollout percentage"
          className="w-24 text-base"
          value={draft}
          disabled={disabled}
          onChange={(e) => setDraft(e.currentTarget.value)}
          onBlur={commit}
          onKeyDown={(e) => {
            if (e.key === "Enter") {
              e.preventDefault();
              commit();
            }
          }}
        />
        <span aria-hidden>%</span>
      </div>
    </div>
  );
}
