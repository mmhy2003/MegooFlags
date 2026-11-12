import { Suspense } from "react";

import { AppShell } from "@/components/shell/app-shell";
import { loadShell } from "@/lib/shell-data";

async function Shell({ children }: { children: React.ReactNode }) {
  const shell = await loadShell();
  return <AppShell shell={shell}>{children}</AppShell>;
}

/** Every signed-in page: the sidebar, the header and the page. */
export default function AppLayout({ children }: { children: React.ReactNode }) {
  return (
    // cacheComponents: the shell reads cookies(), so it renders inside Suspense.
    <Suspense fallback={null}>
      <Shell>{children}</Shell>
    </Suspense>
  );
}
