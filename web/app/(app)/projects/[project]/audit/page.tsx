import { Suspense } from "react";

import { PlaceholderPage } from "@/components/patterns/placeholder-page";
import { projectTrail } from "@/lib/routes";

async function Content({ params }: { params: Promise<{ project: string }> }) {
  const { project } = await params;
  return (
    <PlaceholderPage
      title="Audit log"
      trail={projectTrail(decodeURIComponent(project))}
    />
  );
}

export default function AuditPage({
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
