"use client";

import { SdkKeyList } from "@/components/keys/sdk-key-list";
import { PageHeader } from "@/components/patterns/page-header";
import { projectTrail } from "@/lib/routes";

/** The project's API & SDK keys page: SDK keys per environment. */
export function KeysPage({ project }: { project: string }) {
  return (
    <>
      <PageHeader title="API & SDK keys" trail={projectTrail(project)} />
      <section aria-labelledby="sdk-keys" className="flex flex-col gap-4">
        <h2 id="sdk-keys" className="text-lg font-semibold">
          SDK keys
        </h2>
        <SdkKeyList project={project} />
      </section>
    </>
  );
}
