"use client";

import { useId, useState } from "react";

import { RolloutSlider } from "@/components/patterns/rollout-slider";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { NativeSelect } from "@/components/ui/native-select";
import type { components } from "@/lib/api/schema";
import type { ServeDraft } from "@/lib/flags/config-draft";
import {
  booleanWeights,
  evenWeights,
  fromPercent,
  percentOf,
  toPercent,
  weightsSum,
} from "@/lib/flags/weights";

type FlagType = components["schemas"]["FlagType"];

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
 * What a rule or the default rule serves: one variation, or a rollout. A boolean flag's rollout
 * is a slider for the share served `true`; other flags get a weight input per variation.
 */
export function ServeEditor({
  serve,
  onChange,
  type,
  variations,
  readOnly,
  weightsError,
}: {
  serve: ServeDraft;
  onChange: (serve: ServeDraft) => void;
  type: FlagType;
  /** The flag's variation keys, in order. */
  variations: string[];
  readOnly: boolean;
  /** A problem with the rollout weights, shown below them. */
  weightsError?: string;
}) {
  const id = useId();
  const errorId = `${id}-weights-error`;

  const setMode = (mode: ServeDraft["mode"]) => {
    if (mode === serve.mode) return;
    onChange(
      mode === "rollout"
        ? { mode, bucketBy: "key", weights: evenWeights(variations) }
        : { mode, variation: variations[0] ?? "" },
    );
  };

  const radio = (mode: ServeDraft["mode"], label: string) => (
    <label
      data-hit-area
      className="inline-flex min-h-11 min-w-11 items-center gap-2"
    >
      <input
        type="radio"
        name={`${id}-mode`}
        className="size-5 shrink-0 accent-primary"
        checked={serve.mode === mode}
        disabled={readOnly}
        onChange={() => setMode(mode)}
      />
      {label}
    </label>
  );

  return (
    <div className="flex min-w-0 flex-col gap-3">
      <div
        role="radiogroup"
        aria-label="Serve"
        className="flex flex-wrap gap-x-6"
      >
        {radio("variation", "Serve a variation")}
        {radio("rollout", "Serve a rollout")}
      </div>
      {serve.mode === "variation" ? (
        <div className="flex min-w-0 flex-col gap-2 sm:max-w-xs">
          <Label htmlFor={`${id}-variation`}>Variation</Label>
          <NativeSelect
            id={`${id}-variation`}
            value={serve.variation}
            disabled={readOnly}
            onChange={(e) =>
              onChange({ mode: "variation", variation: e.target.value })
            }
          >
            {variations.map((v) => (
              <option key={v} value={v}>
                {v}
              </option>
            ))}
          </NativeSelect>
        </div>
      ) : (
        <>
          {type === "boolean" ? (
            <RolloutSlider
              label="Percentage serving true"
              value={percentOf(serve.weights, "true")}
              disabled={readOnly}
              onValueChange={(p) =>
                onChange({ ...serve, weights: booleanWeights(p) })
              }
            />
          ) : (
            <div className="flex flex-col gap-3">
              <div className="flex flex-wrap gap-4">
                {serve.weights.map((w, i) => (
                  <WeightInput
                    key={w.variation}
                    id={`${id}-weight-${i}`}
                    label={`Weight of ${w.variation} (%)`}
                    weight={w.weight}
                    disabled={readOnly}
                    errorId={weightsError ? errorId : undefined}
                    onChange={(weight) =>
                      onChange({
                        ...serve,
                        weights: serve.weights.map((x, j) =>
                          j === i ? { ...x, weight } : x,
                        ),
                      })
                    }
                  />
                ))}
              </div>
              <p className="text-sm text-muted-foreground">
                Total: {toPercent(weightsSum(serve.weights))} %
              </p>
            </div>
          )}
          {weightsError && (
            <p id={errorId} className="text-sm text-destructive">
              {weightsError}
            </p>
          )}
          <div className="flex min-w-0 flex-col gap-2 sm:max-w-xs">
            <Label htmlFor={`${id}-bucket`}>Bucket by</Label>
            <Input
              id={`${id}-bucket`}
              className="text-base"
              value={serve.bucketBy}
              disabled={readOnly}
              onChange={(e) => onChange({ ...serve, bucketBy: e.target.value })}
            />
          </div>
        </>
      )}
    </div>
  );
}
