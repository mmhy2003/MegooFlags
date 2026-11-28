import { Suspense } from "react";

import { IntegrationList } from "@/components/integrations/integration-list";

async function Content({ params }: { params: Promise<{ project: string }> }) {
  const { project } = await params;
  return <IntegrationList project={decodeURIComponent(project)} />;
}

export default function IntegrationsPage({
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
