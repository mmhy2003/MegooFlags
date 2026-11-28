import { Suspense } from "react";

import { PageHeader } from "@/components/patterns/page-header";
import { SettingsPage as Settings } from "@/components/settings/settings-page";

export default function SettingsPage() {
  return (
    <>
      <PageHeader title="Settings" />
      <Suspense fallback={null}>
        <Settings />
      </Suspense>
    </>
  );
}
