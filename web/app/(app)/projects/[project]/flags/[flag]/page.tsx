import { Suspense } from "react";

import { PlaceholderPage } from "@/components/patterns/placeholder-page";
import { flagPath, projectTrail } from "@/lib/routes";

async function Content({
  params,
}: {
  params: Promise<{ project: string; flag: string }>;
}) {
  const { project: rawProject, flag: rawFlag } = await params;
  const project = decodeURIComponent(rawProject);
  const flag = decodeURIComponent(rawFlag);
  return (
    <PlaceholderPage
      title={flag}
      trail={[
        ...projectTrail(project),
        { label: flag, href: flagPath(project, flag) },
      ]}
    />
  );
}

export default function FlagPage({
  params,
}: {
  params: Promise<{ project: string; flag: string }>;
}) {
  return (
    <Suspense fallback={null}>
      <Content params={params} />
    </Suspense>
  );
}
