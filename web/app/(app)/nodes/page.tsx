import { Suspense } from "react";

import { NodeList } from "@/components/nodes/node-list";

export default function NodesPage() {
  return (
    <Suspense fallback={null}>
      <NodeList />
    </Suspense>
  );
}
