import { cookies } from "next/headers";
import { redirect } from "next/navigation";
import { FolderPlus } from "lucide-react";
import { Suspense } from "react";

import { EmptyState } from "@/components/patterns/empty-state";
import { serverApi } from "@/lib/api/server";
import { homeTarget } from "@/lib/routes";
import { PROJECT_COOKIE } from "@/lib/theme";

async function Home() {
  const api = await serverApi();
  const c = await cookies();
  const { data } = await api.GET("/api/projects", {
    params: { query: { limit: 500 } },
  });
  const target = homeTarget(
    data?.items.map((p) => p.key) ?? [],
    c.get(PROJECT_COOKIE)?.value ?? null,
  );
  if (target) redirect(target);
  return (
    <EmptyState
      headingLevel={1}
      icon={FolderPlus}
      title="No projects yet"
      description="Projects you can see will appear here."
    />
  );
}

/** Sends you to your last project, else the first one you can see. */
export default function HomePage() {
  return (
    <Suspense fallback={null}>
      <Home />
    </Suspense>
  );
}
