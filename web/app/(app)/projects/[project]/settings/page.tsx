import { Suspense } from "react";

import { ProjectSettings } from "@/components/projects/project-settings";

async function Content({ params }: { params: Promise<{ project: string }> }) {
  const project = decodeURIComponent((await params).project);
  return <ProjectSettings project={project} />;
}

/** The project's name, description and when its flags count as stale. */
export default function ProjectSettingsPage({
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
