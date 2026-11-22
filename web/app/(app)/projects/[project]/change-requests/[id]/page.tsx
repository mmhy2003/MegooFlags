import { Suspense } from "react";

import { ChangeRequestDetail } from "@/components/change-requests/change-request-detail";

async function Content({
  params,
}: {
  params: Promise<{ project: string; id: string }>;
}) {
  const { project, id } = await params;
  return (
    <ChangeRequestDetail
      project={decodeURIComponent(project)}
      id={decodeURIComponent(id)}
    />
  );
}

/** One change request: what it changes, its reviews and its comments. */
export default function ChangeRequestPage({
  params,
}: {
  params: Promise<{ project: string; id: string }>;
}) {
  return (
    <Suspense fallback={null}>
      <Content params={params} />
    </Suspense>
  );
}
