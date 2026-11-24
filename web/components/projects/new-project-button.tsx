"use client";

import { Plus } from "lucide-react";
import { useState } from "react";

import { NewProjectDialog } from "@/components/projects/new-project-dialog";
import { Button } from "@/components/ui/button";

/** Opens the New project dialog; for pages rendered on the server. */
export function NewProjectButton() {
  const [open, setOpen] = useState(false);
  return (
    <>
      <Button className="min-h-11" onClick={() => setOpen(true)}>
        <Plus aria-hidden />
        New project
      </Button>
      <NewProjectDialog open={open} onOpenChange={setOpen} />
    </>
  );
}
