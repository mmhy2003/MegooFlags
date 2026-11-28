import { redirect } from "next/navigation";
import { Suspense } from "react";

import { projectPath } from "@/lib/routes";

async function ToFlags({
  params,
}: {
  params: Promise<{ project: string }>;
}): Promise<never> {
  const { project } = await params;
  redirect(projectPath(decodeURIComponent(project)));
}

export default function ProjectPage({
  params,
}: {
  params: Promise<{ project: string }>;
}) {
  return (
    <Suspense fallback={null}>
      <ToFlags params={params} />
    </Suspense>
  );
}
