"use client";

import { SdkKeyList } from "@/components/keys/sdk-key-list";
import { PageHeader } from "@/components/patterns/page-header";
import { useProjectPermissions } from "@/components/shell/use-project-permissions";
import { ServiceTokenList } from "@/components/tokens/service-token-list";
import { projectTrail } from "@/lib/routes";

/**
 * The project's API & SDK keys page: SDK keys per environment, and the service tokens that can
 * act in the project for people with token.manage.
 */
export function KeysPage({ project }: { project: string }) {
  const perms = useProjectPermissions(project, null, null);
  return (
    <>
      <PageHeader title="API & SDK keys" trail={projectTrail(project)} />
      <section aria-labelledby="sdk-keys" className="flex flex-col gap-4">
        <h2 id="sdk-keys" className="text-lg font-semibold">
          SDK keys
        </h2>
        <SdkKeyList project={project} perms={perms} />
      </section>
      {perms?.org.includes("token.manage") && (
        <section
          aria-labelledby="service-tokens"
          className="mt-8 flex flex-col gap-4"
        >
          <h2 id="service-tokens" className="text-lg font-semibold">
            Service tokens
          </h2>
          <ServiceTokenList project={project} />
        </section>
      )}
    </>
  );
}
