"use client";

import { Trash2 } from "lucide-react";
import { useId, useState } from "react";

import { Button } from "@/components/ui/button";
import { Checkbox } from "@/components/ui/checkbox";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { NativeSelect } from "@/components/ui/native-select";
import { Textarea } from "@/components/ui/textarea";
import type { components } from "@/lib/api/schema";
import type { ClauseDraft } from "@/lib/flags/config-draft";
import {
  formatValues,
  OPERATORS,
  type OperatorKind,
  operatorKind,
  parseValues,
} from "@/lib/flags/operators";

type Operator = components["schemas"]["Operator"];

const HINTS: Record<Exclude<OperatorKind, "segment">, string> = {
  list: "One per line",
  text: "One per line",
  regex: "Regular expressions",
  number: "Numbers",
  semver: "Versions like 1.2.3",
  date: "RFC 3339 date-times like 2026-01-31T00:00:00Z",
};

/** The segments a clause matches, as one checkbox per segment of the project. */
function SegmentPicker({
  segments,
  values,
  readOnly,
  describedBy,
  onChange,
}: {
  segments: string[];
  values: ClauseDraft["values"];
  readOnly: boolean;
  describedBy: string | undefined;
  onChange: (values: string[]) => void;
}) {
  const chosen = values.map(String);
  // Keep a saved key that is no longer listed, so it isn't dropped silently.
  const keys = [...segments, ...chosen.filter((k) => !segments.includes(k))];
  return (
    <fieldset
      className="flex min-w-0 flex-col gap-1"
      aria-describedby={describedBy}
    >
      <legend className="mb-1 text-sm font-medium">Segments</legend>
      {keys.length === 0 ? (
        <p className="text-muted-foreground">
          No segments in this project yet.
        </p>
      ) : (
        <div className="flex flex-wrap gap-x-6">
          {keys.map((key) => (
            <label
              key={key}
              data-hit-area
              className="inline-flex min-h-11 min-w-11 items-center gap-2"
            >
              <Checkbox
                aria-label={`Segment ${key}`}
                checked={chosen.includes(key)}
                disabled={readOnly}
                onChange={(e) =>
                  onChange(
                    e.target.checked
                      ? [...chosen, key]
                      : chosen.filter((k) => k !== key),
                  )
                }
              />
              <span className="min-w-0 break-all">{key}</span>
            </label>
          ))}
        </div>
      )}
    </fieldset>
  );
}

/**
 * One clause of a rule: attribute, operator, Not, and its values (text, one per line, applied
 * on blur when they parse; or segment checkboxes for `is in segment`).
 */
export function ClauseRow({
  clause,
  segments,
  readOnly,
  canRemove,
  error,
  onChange,
  onRemove,
}: {
  clause: ClauseDraft;
  /** The project's segment keys. */
  segments: string[];
  readOnly: boolean;
  /** False for a rule's only clause. */
  canRemove: boolean;
  /** A problem from the draft checks or the API. */
  error: string | undefined;
  onChange: (patch: Partial<Omit<ClauseDraft, "id">>) => void;
  onRemove: () => void;
}) {
  const id = useId();
  const kind = operatorKind(clause.operator);
  const formatted = formatValues(clause.values);
  const [text, setText] = useState(formatted);
  const [shown, setShown] = useState(formatted);
  const [parseError, setParseError] = useState<string | null>(null);
  // Show the draft's values when they change from outside the field (Discard, a save).
  if (shown !== formatted) {
    setShown(formatted);
    setText(formatted);
    setParseError(null);
  }

  // Values that don't parse stay in the field with an error; the draft keeps its values.
  const apply = () => {
    const parsed = parseValues(clause.operator, text);
    if (!parsed.ok) {
      setParseError(parsed.error);
      return;
    }
    setParseError(null);
    if (formatValues(parsed.values) !== formatted)
      onChange({ values: parsed.values });
  };

  // The text is read again for the new operator; values it no longer accepts are cleared.
  const changeOperator = (op: Operator) => {
    setParseError(null);
    const switchesSegment =
      operatorKind(op) === "segment" || kind === "segment";
    const parsed = switchesSegment ? null : parseValues(op, text);
    if (!parsed?.ok) setText("");
    onChange({ operator: op, values: parsed?.ok ? parsed.values : [] });
  };

  const shownError = parseError ?? error;
  const errorId = `${id}-error`;
  const hintId = `${id}-hint`;

  return (
    <div className="flex min-w-0 flex-col gap-3">
      <div className="flex min-w-0 flex-col gap-3 md:flex-row md:items-end">
        <div className="flex min-w-0 flex-col gap-2 md:flex-1">
          <Label htmlFor={`${id}-attribute`}>Attribute</Label>
          <Input
            id={`${id}-attribute`}
            className="h-11 text-base"
            value={clause.attribute}
            readOnly={readOnly}
            onChange={(e) => onChange({ attribute: e.target.value })}
          />
        </div>
        <div className="flex min-w-0 flex-col gap-2 md:flex-1">
          <Label htmlFor={`${id}-operator`}>Operator</Label>
          <NativeSelect
            id={`${id}-operator`}
            value={clause.operator}
            disabled={readOnly}
            onChange={(e) => changeOperator(e.target.value as Operator)}
          >
            {OPERATORS.map((o) => (
              <option key={o.value} value={o.value}>
                {o.label}
              </option>
            ))}
          </NativeSelect>
        </div>
        <div className="flex items-center gap-3">
          <label
            data-hit-area
            className="inline-flex min-h-11 min-w-11 items-center gap-2"
          >
            <Checkbox
              checked={clause.negate}
              disabled={readOnly}
              onChange={(e) => onChange({ negate: e.target.checked })}
            />
            Not
          </label>
          {!readOnly && (
            <Button
              variant="outline"
              size="icon"
              className="min-h-11 min-w-11"
              aria-label="Remove clause"
              disabled={!canRemove}
              onClick={onRemove}
            >
              <Trash2 aria-hidden />
            </Button>
          )}
        </div>
      </div>
      {kind === "segment" ? (
        <SegmentPicker
          segments={segments}
          values={clause.values}
          readOnly={readOnly}
          describedBy={shownError ? errorId : undefined}
          onChange={(values) => onChange({ values })}
        />
      ) : (
        <div className="flex min-w-0 flex-col gap-2">
          <Label htmlFor={`${id}-values`}>Values</Label>
          <Textarea
            id={`${id}-values`}
            className="min-h-20 font-mono text-base"
            value={text}
            readOnly={readOnly}
            aria-invalid={shownError ? true : undefined}
            aria-describedby={shownError ? `${hintId} ${errorId}` : hintId}
            onChange={(e) => setText(e.target.value)}
            onBlur={apply}
          />
          <p id={hintId} className="text-sm text-muted-foreground">
            {HINTS[kind]}
          </p>
        </div>
      )}
      {shownError && (
        <p id={errorId} className="text-sm text-destructive">
          {shownError}
        </p>
      )}
    </div>
  );
}
