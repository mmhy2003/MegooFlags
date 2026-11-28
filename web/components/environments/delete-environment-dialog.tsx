"use client";

import { toast } from "sonner";

import { TypedDeleteDialog } from "@/components/patterns/typed-delete-dialog";
import { api } from "@/lib/api/client";
import type { components } from "@/lib/api/schema";
import { ApiError, failureText, must } from "@/lib/flags/errors";

type Environment = components["schemas"]["EnvironmentOut"];

/** Deletes an environment, with everything in it, once its key is typed. */
export function DeleteEnvironmentDialog({
  project,
  environment,
  open,
  onOpenChange,
  onDeleted,
}: {
  project: string;
  environment: Environment;
  open: boolean;
  onOpenChange: (open: boolean) => void;
  onDeleted: () => void;
}) {
  const remove = async () => {
    try {
      await must(
        api.DELETE("/api/projects/{project}/environments/{env}", {
          params: { path: { project, env: environment.key } },
        }),
      );
    } catch (error) {
      if (!(error instanceof ApiError))
        console.error("deleting the environment failed", error);
      toast.error(
        error instanceof ApiError
          ? failureText(error.failure)
          : "Couldn't reach the server",
      );
      throw error;
    }
    toast("Environment deleted");
    onDeleted();
  };

  return (
    <TypedDeleteDialog
      open={open}
      onOpenChange={onOpenChange}
      title={`Delete ${environment.name}?`}
      description={`This deletes ${environment.name} and, with it, every flag's state, targeting, schedules and rollouts there, its SDK keys, change requests and role assignments. It can't be undone.`}
      typedKey={environment.key}
      confirmLabel="Delete environment"
      onDelete={remove}
    />
  );
}
