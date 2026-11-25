import { Suspense } from "react";

import { SegmentList } from "@/components/segments/segment-list";

async function Content({ params }: { params: Promise<{ project: string }> }) {
  const project = decodeURIComponent((await params).project);
  return <SegmentList project={project} />;
}

/** The project's segments: search, how many flags use each, and New segment. */
export default function SegmentsPage({
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
