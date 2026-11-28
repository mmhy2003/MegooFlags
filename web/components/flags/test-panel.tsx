"use client";

import { useId, useState } from "react";

import { EmptyState } from "@/components/patterns/empty-state";
import { JsonViewer } from "@/components/patterns/json-viewer";
import { Alert, AlertDescription } from "@/components/ui/alert";
import { Button } from "@/components/ui/button";
import { Label } from "@/components/ui/label";
import { Textarea } from "@/components/ui/textarea";
import { api } from "@/lib/api/client";
import type { components } from "@/lib/api/schema";
import { ApiError, failureText, must } from "@/lib/flags/errors";
import { explain } from "@/lib/flags/explain";

type EvaluationResult = components["schemas"]["EvaluationResult"];
type Context = components["schemas"]["EvaluateIn"]["context"];

const DEFAULT_CONTEXT = '{\n  "key": "user-1"\n}';

function storageKey(project: string, flag: string): string {
  return `megooflags:test-context:${project}/${flag}`;
}

/** The context as an object, or null when it isn't a JSON object. */
function parseContext(text: string): Context | null {
  try {
    const value: unknown = JSON.parse(text);
    return typeof value === "object" && value !== null && !Array.isArray(value)
      ? (value as Context)
      : null;
  } catch {
    return null;
  }
}

function ResultValue({ value }: { value: unknown }) {
  if (typeof value === "object" && value !== null)
    return <JsonViewer value={value} label="Value" />;
  return <code className="font-mono break-all">{JSON.stringify(value)}</code>;
}

/**
 * "Test this flag": evaluates the flag's saved config in one environment for a context the user
 * types, and says which variation it gave and why. The context is kept per flag for the session.
 */
export function TestPanel({
  project,
  flag,
  env,
  envName,
  dirty,
  canEvaluate,
  onShowRule,
}: {
  project: string;
  flag: string;
  env: string;
  envName: string;
  dirty: boolean;
  canEvaluate: boolean;
  /** Opens rule `n` (1-based) in Targeting. */
  onShowRule: (n: number) => void;
}) {
  const id = useId();
  const [context, setContext] = useState(() =>
    typeof window === "undefined"
      ? DEFAULT_CONTEXT
      : (window.sessionStorage.getItem(storageKey(project, flag)) ??
        DEFAULT_CONTEXT),
  );
  const [invalid, setInvalid] = useState(false);
  const [alert, setAlert] = useState<string | null>(null);
  const [sending, setSending] = useState(false);
  const [result, setResult] = useState<EvaluationResult | null>(null);

  const change = (text: string) => {
    setContext(text);
    setInvalid(false);
    window.sessionStorage.setItem(storageKey(project, flag), text);
  };

  const evaluate = async (e: React.FormEvent) => {
    e.preventDefault();
    const parsed = parseContext(context);
    setAlert(null);
    if (!parsed) {
      setInvalid(true);
      return;
    }
    setSending(true);
    try {
      const out = await must(
        api.POST("/api/projects/{project}/environments/{env}/evaluate", {
          params: { path: { project, env } },
          body: { context: parsed, flags: [flag] },
        }),
      );
      setResult(out.results[0] ?? null);
    } catch (error) {
      if (!(error instanceof ApiError)) console.error("evaluate failed", error);
      setAlert(
        error instanceof ApiError
          ? failureText(error.failure)
          : "Couldn't reach the server",
      );
    } finally {
      setSending(false);
    }
  };

  const headingId = `${id}-heading`;
  const heading = (
    <h2 id={headingId} className="text-lg font-semibold">
      Test in {envName}
    </h2>
  );

  if (!canEvaluate)
    return (
      <section aria-labelledby={headingId} className="flex flex-col gap-4">
        {heading}
        <EmptyState
          title="You can't evaluate flags in this environment"
          description="Ask an admin for the Evaluate permission here."
        />
      </section>
    );

  const ruleIndex = result?.metadata.rule_index;
  const ruleNumber =
    result?.reason === "TARGETING_MATCH" && typeof ruleIndex === "number"
      ? ruleIndex + 1
      : null;

  return (
    <section
      aria-labelledby={headingId}
      className="flex min-w-0 flex-col gap-4"
    >
      {heading}
      {dirty && (
        <Alert>
          <AlertDescription>
            Results use the saved config. Save your changes to test them.
          </AlertDescription>
        </Alert>
      )}
      <form onSubmit={evaluate} className="flex flex-col gap-3">
        <div className="flex flex-col gap-1.5">
          <Label htmlFor={`${id}-context`}>Context (JSON)</Label>
          <Textarea
            id={`${id}-context`}
            value={context}
            onChange={(e) => change(e.target.value)}
            spellCheck={false}
            aria-invalid={invalid || undefined}
            aria-describedby={invalid ? `${id}-invalid` : undefined}
            className="max-h-80 min-h-32 overflow-auto font-mono text-base md:text-base"
          />
          {invalid && (
            <p id={`${id}-invalid`} className="text-sm text-destructive">
              Enter a JSON object
            </p>
          )}
        </div>
        <div>
          <Button type="submit" disabled={sending}>
            Evaluate
          </Button>
        </div>
      </form>
      {alert && (
        <Alert variant="destructive">
          <AlertDescription>{alert}</AlertDescription>
        </Alert>
      )}
      {result && (
        <section
          aria-labelledby={`${id}-result`}
          className="flex min-w-0 flex-col gap-3"
        >
          <h3 id={`${id}-result`} className="font-semibold">
            Result
          </h3>
          <dl className="grid grid-cols-[auto_minmax(0,1fr)] items-baseline gap-x-4 gap-y-2">
            <dt className="text-muted-foreground">Variation</dt>
            <dd>{result.variant ?? "None"}</dd>
            <dt className="text-muted-foreground">Value</dt>
            <dd className="min-w-0">
              <ResultValue value={result.value} />
            </dd>
            <dt className="text-muted-foreground">Why</dt>
            <dd className="flex flex-wrap items-center gap-2">
              {explain(result)}
              {ruleNumber !== null && (
                <Button
                  variant="outline"
                  onClick={() => onShowRule(ruleNumber)}
                >
                  Show rule {ruleNumber}
                </Button>
              )}
            </dd>
          </dl>
          <JsonViewer value={result} label="Raw result" />
        </section>
      )}
    </section>
  );
}
