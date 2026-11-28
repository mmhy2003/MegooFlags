import Link from "next/link";
import { SearchX } from "lucide-react";

import { EmptyState } from "@/components/patterns/empty-state";
import { buttonVariants } from "@/components/ui/button";

export default function NotFound() {
  return (
    <EmptyState
      headingLevel={1}
      icon={SearchX}
      title="Page not found"
      description="This page doesn't exist, or you can't see it."
    >
      <Link href="/" className={buttonVariants()}>
        Go home
      </Link>
    </EmptyState>
  );
}
