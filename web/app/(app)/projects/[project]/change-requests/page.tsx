import { Suspense } from "react";

import { ChangeRequestList } from "@/components/change-requests/change-request-list";

async function Content({ params }: { params: Promise<{ project: string }> }) {
  const { project } = await params;
  return <ChangeRequestList project={decodeURIComponent(project)} />;
}

export default function ChangeRequestsPage({
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
