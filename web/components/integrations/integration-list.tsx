"use client";

import { Ellipsis, Pencil, Plus, Webhook } from "lucide-react";
import { useState } from "react";

import { IntegrationForm } from "@/components/integrations/integration-form";
import { SecretReveal } from "@/components/integrations/secret-reveal";
import { type Column, DataList } from "@/components/patterns/data-list";
import { EmptyState } from "@/components/patterns/empty-state";
import { PageHeader } from "@/components/patterns/page-header";
import { StatusPill } from "@/components/patterns/status-pill";
import { useProjectPermissions } from "@/components/shell/use-project-permissions";
import { Alert, AlertDescription, AlertTitle } from "@/components/ui/alert";
import { Button } from "@/components/ui/button";
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu";
import { Skeleton } from "@/components/ui/skeleton";
import { useApiData } from "@/hooks/use-api-data";
import { api } from "@/lib/api/client";
import type { components } from "@/lib/api/schema";
import { failureText, must } from "@/lib/flags/errors";
import { can } from "@/lib/flags/permissions";
import { groupLabel, KIND_LABELS } from "@/lib/integrations/groups";
import { integrationStatus, statusPill } from "@/lib/integrations/status";
import { projectTrail } from "@/lib/routes";

type Integration = components["schemas"]["IntegrationOut"];

const DATE_TIME = new Intl.DateTimeFormat(undefined, {
  dateStyle: "medium",
  timeStyle: "short",
});

function columns(onEdit: (i: Integration) => void): Column<Integration>[] {
  return [
    {
      id: "name",
      header: "Integration",
      priority: "key",
      cell: (i) => (
        <div className="flex min-w-0 flex-col">
          <span className="font-medium break-words">{i.name}</span>
          <span className="text-sm text-muted-foreground">
            {KIND_LABELS[i.kind]}
          </span>
        </div>
      ),
    },
    {
      id: "status",
      header: "Status",
      priority: "status",
      cell: (i) => <StatusPill {...statusPill(integrationStatus(i))} />,
    },
    {
      id: "last-delivery",
      header: "Last delivery",
      priority: "main",
      cell: (i) =>
        i.last_delivery_at ? (
          <time dateTime={i.last_delivery_at}>
            {DATE_TIME.format(new Date(i.last_delivery_at))}
          </time>
        ) : (
          "Never"
        ),
    },
    {
      id: "events",
      header: "Events",
      priority: "extra",
      cell: (i) => i.event_groups.map(groupLabel).join(", "),
    },
    {
      id: "environments",
      header: "Environments",
      priority: "extra",
      cell: (i) => (i.all_environments ? "All" : i.environments.join(", ")),
    },
    {
      id: "actions",
      header: "Actions",
      priority: "main",
      cell: (i) => (
        <DropdownMenu>
          <DropdownMenuTrigger
            render={
              <Button
                variant="ghost"
                size="icon"
                aria-label={`Actions for ${i.name}`}
                className="min-h-11 min-w-11"
              />
            }
          >
            <Ellipsis aria-hidden />
          </DropdownMenuTrigger>
          <DropdownMenuContent align="end" className="w-auto min-w-40">
            <DropdownMenuItem onClick={() => onEdit(i)}>
              <Pencil aria-hidden />
              Edit
            </DropdownMenuItem>
          </DropdownMenuContent>
        </DropdownMenu>
      ),
    },
  ];
}

function LoadingList() {
  return (
    <div role="status" className="flex flex-col gap-3">
      <span className="sr-only">Loading integrations</span>
      {[0, 1, 2].map((i) => (
        <Skeleton key={i} className="h-16 w-full" />
      ))}
    </div>
  );
}

/** The project's webhook, Slack and Teams integrations. Only managers see this page's data. */
export function IntegrationList({ project }: { project: string }) {
  const perms = useProjectPermissions(project, null, null);
  const list = useApiData(project, () =>
    must(
      api.GET("/api/projects/{project}/integrations", {
        params: { path: { project } },
      }),
    ),
  );
  // The integration stays set while the dialog closes, so its title doesn't change mid-animation.
  const [form, setForm] = useState<{
    open: boolean;
    integration: Integration | null;
  }>({ open: false, integration: null });
  const openForm = (integration: Integration | null) =>
    setForm({ open: true, integration });
  const closeForm = () => setForm((f) => ({ ...f, open: false }));
  const [secret, setSecret] = useState<string | null>(null);

  const canManage = can(perms, "project.manage");
  const add = canManage && (
    <Button onClick={() => openForm(null)}>
      <Plus aria-hidden />
      Add integration
    </Button>
  );

  let body: React.ReactNode;
  if (list.failure) {
    body = (
      <Alert variant="destructive">
        <AlertTitle>Couldn&apos;t load integrations</AlertTitle>
        <AlertDescription className="flex flex-col items-start gap-2">
          <p>{failureText(list.failure)}</p>
          <Button variant="outline" onClick={list.reload}>
            Try again
          </Button>
        </AlertDescription>
      </Alert>
    );
  } else if (!list.data) {
    body = <LoadingList />;
  } else if (list.data.length === 0) {
    body = (
      <EmptyState
        icon={Webhook}
        title="No integrations yet"
        description="Send flag changes to a webhook, Slack or Microsoft Teams."
      >
        {add}
      </EmptyState>
    );
  } else {
    body = (
      <DataList
        caption="Integrations"
        columns={columns(openForm)}
        rows={list.data}
        rowKey={(i) => i.id}
      />
    );
  }

  return (
    <>
      <PageHeader
        title="Integrations"
        trail={projectTrail(project)}
        actions={add || undefined}
      />
      {body}
      <IntegrationForm
        project={project}
        integration={form.integration}
        open={form.open}
        onOpenChange={(open) => {
          if (!open) closeForm();
        }}
        onSaved={(created) => {
          closeForm();
          if (created?.secret) setSecret(created.secret);
          list.reload();
        }}
      />
      <SecretReveal secret={secret} onClose={() => setSecret(null)} />
    </>
  );
}
