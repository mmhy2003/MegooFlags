"use client";

import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { NativeSelect } from "@/components/ui/native-select";
import {
  EXPIRY_CHOICES,
  type ExpiryChoice,
  type ExpiryValue,
} from "@/lib/tokens/expiry";

/** When a token stops working: a period, never, or the end of a chosen day. */
export function ExpiryField({
  value,
  onChange,
  idPrefix,
  error,
}: {
  value: ExpiryValue;
  onChange: (value: ExpiryValue) => void;
  /** Makes the field ids unique when several forms share a page. */
  idPrefix: string;
  /** Shown under the date, such as "Pick a date". */
  error?: string;
}) {
  const errorId = `${idPrefix}-date-error`;
  return (
    <div className="grid gap-3 sm:grid-cols-2">
      <div className="flex min-w-0 flex-col gap-2">
        <Label htmlFor={`${idPrefix}-choice`}>Expires</Label>
        <NativeSelect
          id={`${idPrefix}-choice`}
          value={value.choice}
          onChange={(e) =>
            onChange({ ...value, choice: e.target.value as ExpiryChoice })
          }
        >
          {EXPIRY_CHOICES.map((c) => (
            <option key={c.value} value={c.value}>
              {c.label}
            </option>
          ))}
        </NativeSelect>
      </div>
      {value.choice === "date" && (
        <div className="flex min-w-0 flex-col gap-2">
          <Label htmlFor={`${idPrefix}-date`}>Expiry date</Label>
          <Input
            id={`${idPrefix}-date`}
            type="date"
            value={value.date}
            onChange={(e) => onChange({ ...value, date: e.target.value })}
            {...(error && {
              "aria-invalid": true,
              "aria-describedby": errorId,
            })}
          />
          {error && (
            <p id={errorId} className="text-sm text-destructive">
              {error}
            </p>
          )}
        </div>
      )}
    </div>
  );
}
