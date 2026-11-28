import { notFound } from "next/navigation";
import { Suspense } from "react";

import { SegmentDetail } from "@/components/segments/segment-detail";
import { serverApi } from "@/lib/api/server";

async function Content({
  params,
}: {
  params: Promise<{ project: string; segment: string }>;
}) {
  const { project: rawProject, segment: rawSegment } = await params;
  const project = decodeURIComponent(rawProject);
  const segment = decodeURIComponent(rawSegment);
  // Checked here so an unknown key gets the not-found page; the page loads the segment itself.
  const api = await serverApi();
  const { response } = await api.GET(
    "/api/projects/{project}/segments/{segment}",
    { params: { path: { project, segment } } },
  );
  if (response.status === 404) notFound();
  return <SegmentDetail project={project} segment={segment} />;
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
