"use client";

import { usePathname, useRouter, useSearchParams } from "next/navigation";
import { useMemo, useState } from "react";
import { toast } from "sonner";

import { copyBlocked, CopyDialog } from "@/components/flags/copy-dialog";
import { type EnvState, EnvStrip } from "@/components/flags/env-strip";
import { ReviewDialog } from "@/components/flags/review-dialog";
import { problemField, RulesEditor } from "@/components/flags/rules-editor";
import { UnsavedBar } from "@/components/flags/unsaved-bar";
import { useUnsavedGuard } from "@/components/flags/use-unsaved-guard";
import { useProjectPermissions } from "@/components/shell/use-project-permissions";
import { Alert, AlertDescription, AlertTitle } from "@/components/ui/alert";
import { Button } from "@/components/ui/button";
import { Skeleton } from "@/components/ui/skeleton";
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs";
import { useApiData } from "@/hooks/use-api-data";
import { api } from "@/lib/api/client";
import type { components } from "@/lib/api/schema";
import {
  type ConfigDraft,
  type DraftProblem,
  isDirty,
  toDraft,
  validateDraft,
  withState,
} from "@/lib/flags/config-draft";
import {
  ApiError,
  type ApiFailure,
  failureText,
  must,
} from "@/lib/flags/errors";
import { can } from "@/lib/flags/permissions";

type FlagConfigOut = components["schemas"]["FlagConfigOut"];

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

/** Problems by the editor field that shows them; the first one wins. */
function byField(problems: DraftProblem[]): Record<string, string> {
  const fields: Record<string, string> = {};
  for (const p of problems) {
    const field = problemField(p.path);
    if (field && !(field in fields)) fields[field] = p.message;
  }
  return fields;
}

function stateOf(config: FlagConfigOut): EnvState {
  return {
    environment: config.environment,
    enabled: config.enabled,
    version: config.version,
    updated_at: config.updated_at,
  };
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

  const segmentList = useApiData(`segments:${project}:all`, () =>
    must(
      api.GET("/api/projects/{project}/segments", {
        params: { path: { project }, query: { limit: 500 } },
      }),
    ),
  );
  const segments = useMemo(
    () => (segmentList.data?.items ?? []).map((s) => s.key),
    [segmentList.data],
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
  // The selected environment's saved config (base) and the draft edited from it. A new config
  // load resets both; a save, a conflict or a toggle moves the base on.
  const [edit, setEdit] = useState<{
    loaded: ConfigDraft | null;
    base: ConfigDraft | null;
    draft: ConfigDraft | null;
  }>({ loaded: null, base: null, draft: null });
  if (edit.loaded !== loaded) setEdit({ loaded, base: loaded, draft: loaded });
  const { base, draft } = edit;
  const dirty = !!base && !!draft && isDirty(base, draft);
  useUnsavedGuard(dirty);

  // Client checks show once Review was pressed; an API 422 shows until the next edit.
  const [checked, setChecked] = useState(false);
  const [invalid, setInvalid] = useState<DraftProblem | null>(null);
  const [reviewing, setReviewing] = useState(false);
  const [copying, setCopying] = useState(false);
  const problems = [
    ...(checked && draft ? validateDraft(draft) : []),
    ...(invalid ? [invalid] : []),
  ];

  const setDraft = (next: ConfigDraft) => {
    setEdit((e) => ({ ...e, draft: next }));
    setInvalid(null);
  };
  const reset = (next: ConfigDraft | null) => {
    setEdit((e) => ({ ...e, base: next, draft: next }));
    setChecked(false);
    setInvalid(null);
  };

  const onState = (state: EnvState) => {
    setChanged((c) => ({ ...c, [state.environment]: state }));
    if (state.environment === env)
      setEdit((e) => ({
        ...e,
        base: e.base && withState(e.base, state),
        draft: e.draft && withState(e.draft, state),
      }));
  };

  // After a copy into the selected environment, its saved config is the new base. A dirty
  // draft is kept on top of it, so Review shows what the draft changes from the copy.
  const onCopied = async (copied: EnvState[]) => {
    setCopying(false);
    setChanged((c) => {
      const next = { ...c };
      for (const s of copied) next[s.environment] = s;
      return next;
    });
    if (!copied.some((s) => s.environment === env)) return;
    try {
      const latest = await must(
        api.GET("/api/projects/{project}/flags/{flag}/environments/{env}", {
          params: { path: { project, flag, env } },
        }),
      );
      const next = toDraft(latest, variationKeys);
      if (dirty)
        setEdit((e) => ({
          ...e,
          base: next,
          draft: e.draft && withState(e.draft, latest),
        }));
      else reset(next);
    } catch (error) {
      if (!(error instanceof ApiError)) console.error("reload failed", error);
      toast.error(
        error instanceof ApiError
          ? failureText(error.failure)
          : "Couldn't reach the server",
      );
    }
  };

  const envRow = envs.find((e) => e.key === env);
  const envName = envRow?.name ?? env;
  const needsApproval = (envRow?.approvals_required ?? 0) > 0;

  const select = (key: string) => {
    if (
      key !== env &&
      dirty &&
      !window.confirm(`Discard unsaved changes in ${envName}?`)
    )
      return;
    setParam("env", key);
  };

  const review = () => {
    if (!draft) return;
    setChecked(true);
    setInvalid(null);
    if (validateDraft(draft).length === 0) setReviewing(true);
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
  const readOnly = archived || !can(perms, "flag.config.write", env);
  const canCopy =
    !archived &&
    envs.some((e) => e.key !== env && copyBlocked(perms, e) === null);

  let targeting: React.ReactNode;
  if (config.failure)
    targeting = (
      <LoadFailure
        what="targeting"
        failure={config.failure}
        onRetry={config.reload}
      />
    );
  else if (!base || !draft) targeting = <Loading what="targeting" />;
  else
    targeting = (
      <div className="flex min-w-0 flex-col gap-4">
        {problems.length > 0 && (
          <Alert variant="destructive">
            <AlertTitle>Fix these before review</AlertTitle>
            <AlertDescription>
              <ul className="list-disc pl-5">
                {problems.map((p) => (
                  <li key={`${p.path}:${p.message}`}>{p.message}</li>
                ))}
              </ul>
            </AlertDescription>
          </Alert>
        )}
        <RulesEditor
          project={project}
          flag={flagData.data}
          draft={draft}
          onChange={setDraft}
          readOnly={readOnly}
          problems={byField(problems)}
          segments={segments}
          onCopy={canCopy ? () => setCopying(true) : undefined}
        />
        <CopyDialog
          open={copying}
          onOpenChange={setCopying}
          project={project}
          flag={flag}
          environments={envs}
          selected={env}
          perms={perms}
          onCopied={onCopied}
        />
        <UnsavedBar
          isDirty={dirty}
          needsApproval={needsApproval}
          onDiscard={() => reset(base)}
          onReview={review}
        />
        <ReviewDialog
          open={reviewing}
          onOpenChange={setReviewing}
          project={project}
          flag={flag}
          env={env}
          envName={envName}
          needsApproval={needsApproval}
          base={base}
          draft={draft}
          onSaved={(saved) => {
            reset(toDraft(saved, variationKeys));
            setChanged((c) => ({ ...c, [env]: stateOf(saved) }));
            setReviewing(false);
          }}
          onConflict={(latest) => {
            setEdit((e) => ({ ...e, base: toDraft(latest, variationKeys) }));
            setChanged((c) => ({ ...c, [env]: stateOf(latest) }));
          }}
          onRequested={() => {
            reset(base);
            setReviewing(false);
          }}
          onInvalid={(failure) => {
            if (!problemField(failure.path)) return false;
            setInvalid({ path: failure.path ?? "", message: failure.message });
            setReviewing(false);
            return true;
          }}
        />
      </div>
    );

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
        onSelect={select}
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
