import { notFound } from "next/navigation";
import { Suspense } from "react";

import { PageHeader } from "@/components/patterns/page-header";
import { serverApi } from "@/lib/api/server";
import { projectPath, projectTrail, segmentPath } from "@/lib/routes";

async function Content({
  params,
}: {
  params: Promise<{ project: string; segment: string }>;
}) {
  const { project: rawProject, segment: rawSegment } = await params;
  const project = decodeURIComponent(rawProject);
  const segment = decodeURIComponent(rawSegment);
  const api = await serverApi();
  const { data, response } = await api.GET(
    "/api/projects/{project}/segments/{segment}",
    { params: { path: { project, segment } } },
  );
  if (response.status === 404) notFound();
  const name = data?.name ?? segment;
  return (
    <PageHeader
      title={name}
      description={<code className="break-all">{segment}</code>}
      trail={[
        ...projectTrail(project),
        { label: "Segments", href: projectPath(project, "segments") },
        { label: name, href: segmentPath(project, segment) },
      ]}
    />
  );
}

/** One segment of a project. */
export default function SegmentPage({
  params,
}: {
  params: Promise<{ project: string; segment: string }>;
}) {
  return (
    <Suspense fallback={null}>
      <Content params={params} />
    </Suspense>
  );
}
