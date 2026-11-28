import { Suspense } from "react";

import { PageHeader } from "@/components/patterns/page-header";
import { PersonalTokenList } from "@/components/tokens/personal-token-list";

export default function TokensPage() {
  return (
    <>
      <PageHeader
        title="API tokens"
        description="Tokens that act as you, for scripts and the CLI."
      />
      <Suspense fallback={null}>
        <PersonalTokenList />
      </Suspense>
    </>
  );
}
