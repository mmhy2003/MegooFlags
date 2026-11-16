"use client";

import {
  Ellipsis,
  History,
  Pause,
  Pencil,
  Play,
  Plus,
  Send,
  Trash2,
  Webhook,
} from "lucide-react";
import { useState } from "react";
import { toast } from "sonner";

import { DeliveryLog } from "@/components/integrations/delivery-log";
import { IntegrationForm } from "@/components/integrations/integration-form";
import { SecretReveal } from "@/components/integrations/secret-reveal";
import { ConfirmDialog } from "@/components/patterns/confirm-dialog";
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
  DropdownMenuSeparator,
  DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu";
import { Skeleton } from "@/components/ui/skeleton";
import { useApiData } from "@/hooks/use-api-data";
import { api } from "@/lib/api/client";
import type { components } from "@/lib/api/schema";
import { ApiError, failureText, must } from "@/lib/flags/errors";
import { can } from "@/lib/flags/permissions";
import { groupLabel, KIND_LABELS } from "@/lib/integrations/groups";
import {
  integrationStatus,
  statusPill,
  testSendResult,
} from "@/lib/integrations/status";
import { projectTrail } from "@/lib/routes";

type Integration = components["schemas"]["IntegrationOut"];

const DATE_TIME = new Intl.DateTimeFormat(undefined, {
  dateStyle: "medium",
  timeStyle: "short",
});

type RowActions = {
  onDeliveries: (i: Integration) => void;
  onEdit: (i: Integration) => void;
  onSendTest: (i: Integration) => void;
  onToggle: (i: Integration) => void;
  onDelete: (i: Integration) => void;
};

function showError(error: unknown, what: string) {
  if (!(error instanceof ApiError)) console.error(what, error);
  toast.error(
    error instanceof ApiError
      ? failureText(error.failure)
      : "Couldn't reach the server",
  );
}

function columns(actions: RowActions): Column<Integration>[] {
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
            <DropdownMenuItem onClick={() => actions.onDeliveries(i)}>
              <History aria-hidden />
              Deliveries
            </DropdownMenuItem>
            <DropdownMenuItem onClick={() => actions.onEdit(i)}>
              <Pencil aria-hidden />
              Edit
            </DropdownMenuItem>
            <DropdownMenuItem onClick={() => actions.onSendTest(i)}>
              <Send aria-hidden />
              Send test
            </DropdownMenuItem>
            <DropdownMenuItem onClick={() => actions.onToggle(i)}>
              {i.enabled ? <Pause aria-hidden /> : <Play aria-hidden />}
              {i.enabled ? "Pause" : "Resume"}
            </DropdownMenuItem>
            <DropdownMenuSeparator />
            <DropdownMenuItem
              variant="destructive"
              onClick={() => actions.onDelete(i)}
            >
              <Trash2 aria-hidden />
              Delete
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
  // Like the form, these keep their integration while closing.
  const [log, setLog] = useState<{
    open: boolean;
    integration: Integration | null;
  }>({ open: false, integration: null });
  const [removing, setRemoving] = useState<{
    open: boolean;
    integration: Integration | null;
  }>({ open: false, integration: null });

  const path = (i: Integration) => ({ project, id: i.id });

  const sendTest = async (i: Integration) => {
    try {
      const delivery = await must(
        api.POST("/api/projects/{project}/integrations/{id}/test", {
          params: { path: path(i) },
        }),
      );
      const result = testSendResult(delivery);
      if (result.ok) toast(result.message);
      else toast.error(result.message);
    } catch (error) {
      showError(error, "sending a test failed");
    }
    list.reload();
  };

  const toggle = async (i: Integration) => {
    try {
      await must(
        api.PATCH("/api/projects/{project}/integrations/{id}", {
          params: { path: path(i) },
          body: { enabled: !i.enabled },
        }),
      );
      toast(i.enabled ? "Integration paused" : "Integration resumed");
      list.reload();
    } catch (error) {
      showError(error, "pausing or resuming the integration failed");
    }
  };

  const remove = async (i: Integration) => {
    try {
      await must(
        api.DELETE("/api/projects/{project}/integrations/{id}", {
          params: { path: path(i) },
        }),
      );
      toast("Integration deleted");
      list.reload();
    } catch (error) {
      showError(error, "deleting the integration failed");
    }
  };

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
        columns={columns({
          onDeliveries: (integration) => setLog({ open: true, integration }),
          onEdit: openForm,
          onSendTest: (integration) => void sendTest(integration),
          onToggle: (integration) => void toggle(integration),
          onDelete: (integration) => setRemoving({ open: true, integration }),
        })}
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
      <DeliveryLog
        project={project}
        integration={log.integration}
        open={log.open}
        onOpenChange={(open) => setLog((l) => ({ ...l, open }))}
      />
      <ConfirmDialog
        open={removing.open}
        onOpenChange={(open) => setRemoving((r) => ({ ...r, open }))}
        title="Delete integration?"
        description={`${removing.integration?.name ?? "It"} stops receiving events, and its delivery log is deleted.`}
        confirmLabel="Delete integration"
        destructive
        onConfirm={() => {
          if (removing.integration) void remove(removing.integration);
        }}
      />
    </>
  );
}
