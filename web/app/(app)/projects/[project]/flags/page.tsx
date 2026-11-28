import { Suspense } from "react";

import { FlagList } from "@/components/flags/flag-list";

async function Content({ params }: { params: Promise<{ project: string }> }) {
  const { project } = await params;
  return <FlagList project={decodeURIComponent(project)} />;
}

export default function FlagsPage({
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
