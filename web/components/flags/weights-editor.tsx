"use client";

import { useId, useState } from "react";

import { RolloutSlider } from "@/components/patterns/rollout-slider";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import type { components } from "@/lib/api/schema";
import {
  booleanWeights,
  fromPercent,
  percentOf,
  toPercent,
  weightsSum,
} from "@/lib/flags/weights";

type FlagType = components["schemas"]["FlagType"];
type Weight = components["schemas"]["WeightIn"];

/** A weight's percent input; it keeps what is typed and passes on each valid number. */
function WeightInput({
  id,
  label,
  weight,
  disabled,
  errorId,
  onChange,
}: {
  id: string;
  label: string;
  weight: number;
  disabled: boolean;
  /** The id of the weights' error, when there is one. */
  errorId: string | undefined;
  onChange: (weight: number) => void;
}) {
  const [text, setText] = useState(String(toPercent(weight)));
  const [shown, setShown] = useState(weight);
  // Show the new weight when it changes from outside the input.
  if (shown !== weight) {
    setShown(weight);
    if (fromPercent(Number(text)) !== weight)
      setText(String(toPercent(weight)));
  }
  return (
    <div className="flex min-w-0 flex-col gap-2">
      <Label htmlFor={id}>{label}</Label>
      <Input
        id={id}
        type="number"
        inputMode="decimal"
        step={0.1}
        min={0}
        max={100}
        className="w-32 text-base"
        value={text}
        disabled={disabled}
        aria-invalid={errorId ? true : undefined}
        aria-describedby={errorId}
        onChange={(e) => {
          const value = e.currentTarget.value;
          setText(value);
          const n = Number(value);
          if (value.trim() !== "" && Number.isFinite(n)) {
            const next = fromPercent(n);
            setShown(next);
            onChange(next);
          }
        }}
      />
    </div>
  );
}

/**
 * A rollout's weights: a slider for the share served `true` on a boolean flag, a weight input
 * per variation otherwise, and a problem with them below.
 */
export function WeightsEditor({
  type,
  weights,
  onChange,
  readOnly,
  error,
}: {
  type: FlagType;
  weights: Weight[];
  onChange: (weights: Weight[]) => void;
  readOnly: boolean;
  /** A problem with the weights, shown below them. */
  error?: string;
}) {
  const id = useId();
  const errorId = `${id}-weights-error`;

  return (
    <>
      {type === "boolean" ? (
        <RolloutSlider
          label="Percentage serving true"
          value={percentOf(weights, "true")}
          disabled={readOnly}
          onValueChange={(p) => onChange(booleanWeights(p))}
        />
      ) : (
        <div className="flex flex-col gap-3">
          <div className="flex flex-wrap gap-4">
            {weights.map((w, i) => (
              <WeightInput
                key={w.variation}
                id={`${id}-weight-${i}`}
                label={`Weight of ${w.variation} (%)`}
                weight={w.weight}
                disabled={readOnly}
                errorId={error ? errorId : undefined}
                onChange={(weight) =>
                  onChange(
                    weights.map((x, j) => (j === i ? { ...x, weight } : x)),
                  )
                }
              />
            ))}
          </div>
          <p className="text-sm text-muted-foreground">
            Total: {toPercent(weightsSum(weights))} %
          </p>
        </div>
      )}
      {error && (
        <p id={errorId} className="text-sm text-destructive">
          {error}
        </p>
      )}
    </>
  );
}
