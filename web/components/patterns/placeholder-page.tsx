import { Construction } from "lucide-react";

import { EmptyState } from "@/components/patterns/empty-state";
import { PageHeader } from "@/components/patterns/page-header";
import type { Crumb } from "@/lib/routes";

/** A menu page whose real content arrives later (MF-82). */
export function PlaceholderPage({
  title,
  trail,
}: {
  title: string;
  trail?: Crumb[];
}) {
  return (
    <>
      <PageHeader title={title} trail={trail} />
      <EmptyState
        icon={Construction}
        title="Coming soon"
        description="This page arrives in a later release."
      />
    </>
  );
}
