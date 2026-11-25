"use client";

import { useId } from "react";

import { WeightsEditor } from "@/components/flags/weights-editor";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { NativeSelect } from "@/components/ui/native-select";
import type { components } from "@/lib/api/schema";
import type { ServeDraft } from "@/lib/flags/config-draft";
import { evenWeights } from "@/lib/flags/weights";

type FlagType = components["schemas"]["FlagType"];

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
          <WeightsEditor
            type={type}
            weights={serve.weights}
            readOnly={readOnly}
            error={weightsError}
            onChange={(weights) => onChange({ ...serve, weights })}
          />
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
