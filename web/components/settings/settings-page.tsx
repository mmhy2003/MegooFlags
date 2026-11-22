"use client";

import { usePathname, useRouter, useSearchParams } from "next/navigation";

import { AuditLog } from "@/components/audit/audit-log";
import { LoadFailed, LoadingRows } from "@/components/patterns/list-state";
import { SecurityTab } from "@/components/settings/security-tab";
import { ServiceTokenList } from "@/components/tokens/service-token-list";
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs";
import { useApiData } from "@/hooks/use-api-data";
import { api } from "@/lib/api/client";
import { must } from "@/lib/flags/errors";
import {
  pickTab,
  type SettingsTab,
  TAB_LABELS,
  visibleTabs,
} from "@/lib/settings/tabs";

function Panel({ tab }: { tab: SettingsTab }) {
  switch (tab) {
    case "tokens":
      return <ServiceTokenList />;
    case "security":
      return <SecurityTab />;
    case "audit":
      return <AuditLog scope={{ kind: "org" }} />;
    default:
      return null;
  }
}

/**
 * The organization's settings, one tab at a time (`?tab=`). Each tab shows by the caller's org
 * permissions; an unknown or hidden tab falls back to the first visible one.
 */
export function SettingsPage() {
  const router = useRouter();
  const pathname = usePathname();
  const params = useSearchParams();
  const perms = useApiData("me-permissions", () =>
    must(api.GET("/api/auth/me/permissions")),
  );

  if (perms.failure) {
    return (
      <LoadFailed
        title="Couldn't load your permissions"
        failure={perms.failure}
        onRetry={perms.reload}
      />
    );
  }
  if (!perms.data) return <LoadingRows label="Loading the settings" />;

  const visible = visibleTabs(perms.data.org);
  const active = pickTab(params.get("tab"), visible);

  // The other tabs' params (filters) mean nothing on the new one, so only the tab is kept.
  const select = (tab: string) => {
    router.replace(`${pathname}?tab=${encodeURIComponent(tab)}`, {
      scroll: false,
    });
  };

  return (
    <Tabs
      value={active}
      onValueChange={(value) => select(String(value))}
      className="gap-6"
    >
      <div className="max-w-full overflow-x-auto">
        <TabsList
          aria-label="Settings"
          className="group-data-horizontal/tabs:h-auto"
        >
          {visible.map((tab) => (
            <TabsTrigger key={tab} value={tab} className="min-h-11 px-3">
              {TAB_LABELS[tab]}
            </TabsTrigger>
          ))}
        </TabsList>
      </div>
      <TabsContent value={active} className="min-w-0">
        <Panel tab={active} />
      </TabsContent>
    </Tabs>
  );
}
