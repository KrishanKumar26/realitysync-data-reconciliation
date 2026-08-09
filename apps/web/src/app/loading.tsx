import { Panel, PanelBody } from "@/components/ui/panel";
import { Skeleton } from "@/components/ui/skeleton";

/** Route-level loading state. Geometry matches the loaded layout. */
export default function Loading() {
  return (
    <div className="space-y-6">
      <div className="space-y-2">
        <Skeleton className="h-6 w-40" />
        <Skeleton className="h-4 w-64" />
      </div>
      <Panel>
        <PanelBody className="space-y-3">
          <Skeleton className="h-4 w-52" />
          <Skeleton className="h-4 w-36" />
          <Skeleton className="h-4 w-44" />
        </PanelBody>
      </Panel>
    </div>
  );
}
