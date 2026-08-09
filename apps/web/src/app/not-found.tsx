import Link from "next/link";

import { buttonVariants } from "@/components/ui/button";
import { Panel, PanelBody } from "@/components/ui/panel";
import { EmptyState } from "@/components/ui/states";

export default function NotFound() {
  return (
    <Panel>
      <PanelBody className="p-0">
        <EmptyState
          title="Page not found"
          description="That destination does not exist in this workspace."
          action={
            <Link href="/" className={buttonVariants({ variant: "secondary", size: "sm" })}>
              Back to overview
            </Link>
          }
        />
      </PanelBody>
    </Panel>
  );
}
