import { Panel, PanelBody, PanelHeader } from "@/components/ui/panel";
import { EmptyState } from "@/components/ui/states";

/**
 * Honest placeholder for a navigation destination that is not built yet.
 *
 * The alternative — a screen of invented sources, entities or conflicts —
 * would make the interface lie about what the system can do.
 */
export function PhasePlaceholder({
  title,
  description,
  phase,
  detail,
}: {
  title: string;
  description: string;
  phase: number;
  detail: string;
}) {
  return (
    <div className="space-y-6">
      <header>
        <h1 className="text-xl font-semibold tracking-tight">{title}</h1>
        <p className="mt-1.5 text-sm text-muted-foreground">{description}</p>
      </header>

      <Panel>
        <PanelHeader title={title} />
        <PanelBody className="p-0">
          <EmptyState
            title={`Available in Phase ${phase}`}
            description={detail}
          />
        </PanelBody>
      </Panel>
    </div>
  );
}
