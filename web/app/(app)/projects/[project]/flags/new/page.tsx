import { Suspense } from "react";

import { FlagForm } from "@/components/flags/flag-form";
import { PageHeader } from "@/components/patterns/page-header";
import { newFlagPath, projectTrail } from "@/lib/routes";

async function Content({ params }: { params: Promise<{ project: string }> }) {
  const project = decodeURIComponent((await params).project);
  return (
    <>
      <PageHeader
        title="New flag"
        trail={[
          ...projectTrail(project),
          { label: "New flag", href: newFlagPath(project) },
        ]}
      />
      <FlagForm project={project} />
    </>
  );
}

export default function NewFlagPage({
  params,
}: {
  params: Promise<{ project: string }>;
}) {
  return (
    <Suspense fallback={null}>
      <Content params={params} />
    </Suspense>
  );
}
