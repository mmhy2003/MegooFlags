"use client";

import { usePathname, useRouter, useSearchParams } from "next/navigation";
import { useMemo, useState } from "react";

import { type EnvState, EnvStrip } from "@/components/flags/env-strip";
import { useProjectPermissions } from "@/components/shell/use-project-permissions";
import { Alert, AlertDescription, AlertTitle } from "@/components/ui/alert";
import { Button } from "@/components/ui/button";
import { Skeleton } from "@/components/ui/skeleton";
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs";
import { useApiData } from "@/hooks/use-api-data";
import { api } from "@/lib/api/client";
import { type ConfigDraft, toDraft, withState } from "@/lib/flags/config-draft";
import { type ApiFailure, failureText, must } from "@/lib/flags/errors";

const TABS = [
  { value: "targeting", label: "Targeting" },
  { value: "test", label: "Test" },
  { value: "history", label: "History" },
  { value: "insights", label: "Insights" },
  { value: "settings", label: "Settings" },
] as const;

type Tab = (typeof TABS)[number]["value"];

function isTab(value: string | null): value is Tab {
  return TABS.some((t) => t.value === value);
}

function LoadFailure({
  what,
  failure,
  onRetry,
}: {
  what: string;
  failure: ApiFailure;
  onRetry: () => void;
}) {
  return (
    <Alert variant="destructive">
      <AlertTitle>Couldn&apos;t load {what}</AlertTitle>
      <AlertDescription className="flex flex-col items-start gap-2">
        <p>{failureText(failure)}</p>
        <Button variant="outline" onClick={onRetry}>
          Try again
        </Button>
      </AlertDescription>
    </Alert>
  );
}

function Loading({ what }: { what: string }) {
  return (
    <div role="status" className="flex flex-col gap-3">
      <span className="sr-only">Loading {what}</span>
      <Skeleton className="h-40 w-full" />
    </div>
  );
}

function ComingSoon() {
  return <p className="text-muted-foreground">Coming in this release.</p>;
}

/**
 * The flag detail page below its header: the environment strip, then the tabs. The selected
 * environment and tab live in the URL (`?env=&tab=`); the selected environment's config is
 * loaded into a draft the editor works on.
 */
export function FlagDetail({
  project,
  flag,
}: {
  project: string;
  flag: string;
}) {
  const router = useRouter();
  const pathname = usePathname();
  const params = useSearchParams();
  const perms = useProjectPermissions(project, null, null);

  const flagData = useApiData(`flag:${project}/${flag}`, () =>
    must(
      api.GET("/api/projects/{project}/flags/{flag}", {
        params: { path: { project, flag } },
      }),
    ),
  );
  const environments = useApiData(`environments:${project}`, () =>
    must(
      api.GET("/api/projects/{project}/environments", {
        params: { path: { project } },
      }),
    ),
  );

  const envs = environments.data ?? [];
  const wanted = params.get("env");
  const env = envs.some((e) => e.key === wanted)
    ? (wanted as string)
    : (envs[0]?.key ?? "");
  const rawTab = params.get("tab");
  const tab: Tab = isTab(rawTab) ? rawTab : "targeting";

  const setParam = (name: "env" | "tab", value: string) => {
    const next = new URLSearchParams(params);
    next.set(name, value);
    router.replace(`${pathname}?${next.toString()}`, { scroll: false });
  };

  const configKey = env ? `config:${project}/${flag}/${env}` : null;
  const config = useApiData(configKey, () =>
    must(
      api.GET("/api/projects/{project}/flags/{flag}/environments/{env}", {
        params: { path: { project, flag, env } },
      }),
    ),
  );

  // States changed here since the flag loaded, by environment.
  const [changed, setChanged] = useState<Record<string, EnvState>>({});
  const states = useMemo(() => {
    const byEnv: Record<string, EnvState> = {};
    for (const s of flagData.data?.environments ?? []) byEnv[s.environment] = s;
    return { ...byEnv, ...changed };
  }, [flagData.data, changed]);

  const variationKeys = useMemo(
    () => (flagData.data?.variations ?? []).map((v) => v.key),
    [flagData.data],
  );
  const loaded = useMemo(
    () => (config.data ? toDraft(config.data, variationKeys) : null),
    [config.data, variationKeys],
  );
  // The draft of the selected environment; edits replace it, a new config load resets it.
  const [edited, setEdited] = useState<{
    base: ConfigDraft;
    draft: ConfigDraft;
  } | null>(null);
  const draft =
    loaded && edited?.base === loaded ? edited.draft : (loaded ?? null);

  const onState = (state: EnvState) => {
    setChanged((c) => ({ ...c, [state.environment]: state }));
    if (state.environment === env && loaded && draft)
      setEdited({ base: loaded, draft: withState(draft, state) });
  };

  if (flagData.failure)
    return (
      <LoadFailure
        what="the flag"
        failure={flagData.failure}
        onRetry={flagData.reload}
      />
    );
  if (environments.failure)
    return (
      <LoadFailure
        what="environments"
        failure={environments.failure}
        onRetry={environments.reload}
      />
    );
  if (!flagData.data || !environments.data) return <Loading what="flag" />;

  const archived = flagData.data.archived;

  let targeting: React.ReactNode;
  if (config.failure)
    targeting = (
      <LoadFailure
        what="targeting"
        failure={config.failure}
        onRetry={config.reload}
      />
    );
  else if (!draft) targeting = <Loading what="targeting" />;
  else targeting = <ComingSoon />;

  return (
    <div className="flex min-w-0 flex-col gap-6">
      {archived && (
        <Alert>
          <AlertTitle>This flag is archived</AlertTitle>
          <AlertDescription>
            Restore it in Settings to change it again.
          </AlertDescription>
        </Alert>
      )}
      <EnvStrip
        project={project}
        flag={flag}
        environments={envs}
        states={states}
        perms={perms}
        readOnly={archived}
        selected={env}
        onSelect={(key) => setParam("env", key)}
        onState={onState}
      />
      <Tabs
        value={tab}
        onValueChange={(value) => setParam("tab", String(value))}
      >
        <div className="max-w-full overflow-x-auto">
          <TabsList className="group-data-horizontal/tabs:h-auto">
            {TABS.map((t) => (
              <TabsTrigger
                key={t.value}
                value={t.value}
                className="min-h-11 px-3"
              >
                {t.label}
              </TabsTrigger>
            ))}
          </TabsList>
        </div>
        <TabsContent value="targeting" className="pt-4">
          {targeting}
        </TabsContent>
        {TABS.filter((t) => t.value !== "targeting").map((t) => (
          <TabsContent key={t.value} value={t.value} className="pt-4">
            <ComingSoon />
          </TabsContent>
        ))}
      </Tabs>
    </div>
  );
}
