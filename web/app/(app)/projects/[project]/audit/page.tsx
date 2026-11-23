import { Suspense } from "react";

import { AuditLog } from "@/components/audit/audit-log";
import { PageHeader } from "@/components/patterns/page-header";
import { projectTrail } from "@/lib/routes";

async function Content({ params }: { params: Promise<{ project: string }> }) {
  const project = decodeURIComponent((await params).project);
  return (
    <>
      <PageHeader title="Audit log" trail={projectTrail(project)} />
      <AuditLog scope={{ kind: "project", project }} />
    </>
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
