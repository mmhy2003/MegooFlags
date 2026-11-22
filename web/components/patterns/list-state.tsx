"use client";

import { Alert, AlertDescription, AlertTitle } from "@/components/ui/alert";
import { Button } from "@/components/ui/button";
import { Skeleton } from "@/components/ui/skeleton";
import { type ApiFailure, failureText } from "@/lib/flags/errors";

/** A list that couldn't load: why, and a way to try again. */
export function LoadFailed({
  title,
  failure,
  onRetry,
}: {
  /** Such as "Couldn't load integrations". */
  title: string;
  failure: ApiFailure;
  onRetry: () => void;
}) {
  return (
    <Alert variant="destructive">
      <AlertTitle>{title}</AlertTitle>
      <AlertDescription className="flex flex-col items-start gap-2">
        <p>{failureText(failure)}</p>
        <Button variant="outline" onClick={onRetry}>
          Try again
        </Button>
      </AlertDescription>
    </Alert>
  );
}

/** Placeholder rows while a list loads; `label` is read out, such as "Loading members". */
export function LoadingRows({
  label,
  rows = 3,
}: {
  label: string;
  rows?: number;
}) {
  return (
    <div role="status" className="flex flex-col gap-3">
      <span className="sr-only">{label}</span>
      {Array.from({ length: rows }, (_, i) => (
        <Skeleton key={i} className="h-16 w-full" />
      ))}
    </div>
  );
}
