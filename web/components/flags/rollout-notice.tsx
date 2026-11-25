"use client";

import Link from "next/link";

import { Alert, AlertDescription, AlertTitle } from "@/components/ui/alert";
import type { components } from "@/lib/api/schema";
import { currentPercent, variationText } from "@/lib/flags/rollout-plan";

type Rollout = components["schemas"]["ProgressiveRolloutOut"];

/** Targeting's notice while a progressive rollout moves the default rule. */
export function RolloutNotice({
  rollout,
  href,
}: {
  rollout: Rollout;
  /** The Automation tab. */
  href: string;
}) {
  const state = rollout.status === "paused" ? "paused" : "running";
  return (
    <Alert>
      <AlertTitle>
        A progressive rollout is {state}: the default rule serves{" "}
        {variationText(rollout.to_variation)} to {currentPercent(rollout)} %
        now.
      </AlertTitle>
      <AlertDescription className="flex flex-col items-start gap-1">
        <p>
          Saving another default rule makes the rollout fail at its next step.
        </p>
        <Link
          href={href}
          replace
          scroll={false}
          className="inline-flex min-h-11 items-center text-primary underline underline-offset-4"
        >
          Open Automation
        </Link>
      </AlertDescription>
    </Alert>
  );
}
