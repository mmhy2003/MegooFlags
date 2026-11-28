import { Suspense } from "react";

import { EnvironmentList } from "@/components/environments/environment-list";

async function Content({ params }: { params: Promise<{ project: string }> }) {
  const project = decodeURIComponent((await params).project);
  return <EnvironmentList project={project} />;
}

/** The project's environments in order, with how changes are made in each. */
export default function EnvironmentsPage({
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
