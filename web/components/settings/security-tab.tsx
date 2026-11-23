"use client";

import { useState } from "react";
import { toast } from "sonner";

import { LoadFailed, LoadingRows } from "@/components/patterns/list-state";
import { Switch } from "@/components/ui/switch";
import { useApiData } from "@/hooks/use-api-data";
import { api } from "@/lib/api/client";
import { ApiError, failureText, must } from "@/lib/flags/errors";

const LABEL = "Require two-factor sign-in";

/** The organization's sign-in rules: the Require two-factor switch. Needs org.manage. */
export function SecurityTab() {
  const settings = useApiData("org-settings", () =>
    must(api.GET("/api/settings")),
  );
  // The value the last save returned; it wins over the loaded one.
  const [saved, setSaved] = useState<boolean | null>(null);
  const [busy, setBusy] = useState(false);

  if (settings.failure) {
    return (
      <LoadFailed
        title="Couldn't load the security settings"
        failure={settings.failure}
        onRetry={settings.reload}
      />
    );
  }
  if (!settings.data) {
    return <LoadingRows label="Loading the security settings" rows={1} />;
  }
  const requireMfa = saved ?? settings.data.require_mfa;

  const change = async (value: boolean) => {
    setBusy(true);
    try {
      const result = await must(
        api.PUT("/api/settings", { body: { require_mfa: value } }),
      );
      setSaved(result.require_mfa);
      toast(
        result.require_mfa
          ? "Two-factor sign-in is now required"
          : "Two-factor sign-in is no longer required",
      );
    } catch (error) {
      if (!(error instanceof ApiError)) {
        console.error("saving the security settings failed", error);
        toast.error("Couldn't reach the server");
      } else {
        toast.error(failureText(error.failure));
      }
    } finally {
      setBusy(false);
    }
  };

  return (
    <section
      aria-labelledby="security-mfa"
      className="flex max-w-2xl flex-col gap-2"
    >
      <label
        data-hit-area
        className="inline-flex min-h-11 min-w-11 items-center gap-3"
      >
        <Switch
          checked={requireMfa}
          onCheckedChange={(value) => void change(value)}
          aria-label={LABEL}
          aria-describedby="security-mfa-help"
          disabled={busy}
        />
        <span id="security-mfa" className="font-medium">
          {LABEL}
        </span>
      </label>
      <p id="security-mfa-help" className="text-sm text-muted-foreground">
        People who sign in with a password must set up a second factor. SSO
        users rely on their identity provider.
      </p>
    </section>
  );
}
