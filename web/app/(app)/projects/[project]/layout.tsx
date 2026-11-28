import { notFound } from "next/navigation";
import { Suspense } from "react";

import { serverApi } from "@/lib/api/server";

async function ProjectGate({
  params,
  children,
}: {
  params: Promise<{ project: string }>;
  children: React.ReactNode;
}) {
  const { project } = await params;
  const api = await serverApi();
  const { response } = await api.GET("/api/projects/{project}", {
    params: { path: { project: decodeURIComponent(project) } },
  });
  if (response.status === 404) notFound();
  return children;
}

/** Project pages; an unknown or hidden project is the 404 page inside the shell. */
export default function ProjectLayout({
  params,
  children,
}: {
  params: Promise<{ project: string }>;
  children: React.ReactNode;
}) {
  return (
    <Suspense fallback={null}>
      <ProjectGate params={params}>{children}</ProjectGate>
    </Suspense>
  );
}
