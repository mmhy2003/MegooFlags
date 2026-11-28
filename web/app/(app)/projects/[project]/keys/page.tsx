import { Suspense } from "react";

import { KeysPage } from "@/components/keys/keys-page";

async function Content({ params }: { params: Promise<{ project: string }> }) {
  const project = decodeURIComponent((await params).project);
  return <KeysPage project={project} />;
}

export default function ProjectKeysPage({
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
