import { notFound } from "next/navigation";
import { Suspense } from "react";

import { FlagDetail } from "@/components/flags/flag-detail";
import { PageHeader } from "@/components/patterns/page-header";
import { serverApi } from "@/lib/api/server";
import { flagPath, projectTrail } from "@/lib/routes";

async function Content({
  params,
}: {
  params: Promise<{ project: string; flag: string }>;
}) {
  const { project: rawProject, flag: rawFlag } = await params;
  const project = decodeURIComponent(rawProject);
  const flag = decodeURIComponent(rawFlag);
  const api = await serverApi();
  const { data, response } = await api.GET(
    "/api/projects/{project}/flags/{flag}",
    { params: { path: { project, flag } } },
  );
  if (response.status === 404) notFound();
  return (
    <>
      <PageHeader
        title={data?.name ?? flag}
        description={<code className="break-all">{flag}</code>}
        trail={[
          ...projectTrail(project),
          { label: flag, href: flagPath(project, flag) },
        ]}
      />
      <Suspense fallback={null}>
        <FlagDetail project={project} flag={flag} />
      </Suspense>
    </>
  );
}

/** A flag: its environments, then targeting, test, history, insights and settings tabs. */
export default function FlagPage({
  params,
}: {
  params: Promise<{ project: string; flag: string }>;
}) {
  return (
    <Suspense fallback={null}>
      <Content params={params} />
    </Suspense>
  );
}
