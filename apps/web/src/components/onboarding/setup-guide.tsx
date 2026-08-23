"use client";

import { ArrowRight, Check, Rocket } from "lucide-react";
import Link from "next/link";

import { Badge } from "@/components/ui/badge";
import { buttonVariants } from "@/components/ui/button";
import { Panel, PanelBody, PanelHeader } from "@/components/ui/panel";
import type { Dashboard } from "@/lib/dashboard";
import { cn } from "@/lib/utils";

/**
 * Getting a new workspace to its first answer.
 *
 * Six things have to happen before this product shows anything, and until now
 * a new workspace was told none of them. You landed on an empty dashboard,
 * and if you got one step wrong — a table added but not synced, an item
 * created but not linked — every screen stayed blank with no clue which step
 * was missing. Two people who built this took two hours to walk it once.
 *
 * **Every step is derived from a real count**, all of which the dashboard
 * already returned. Nothing is stored, nothing is marked "done" by pressing
 * anything, and there is no separate progress record to drift out of sync
 * with the data. A step is complete when the thing it describes actually
 * exists — which also means undoing something correctly reopens its step.
 */

export interface SetupStep {
  title: string;
  /** What this step gets you — not what it does. */
  detail: string;
  done: boolean;
  href: string;
  action: string;
}

export function setupSteps(dashboard: Dashboard): SetupStep[] {
  const { sources, ingestion, confidence } = dashboard;

  return [
    {
      title: "Connect a database",
      detail:
        "PostgreSQL or MySQL, reachable from the internet, with an account that can read. RealitySync never writes to it.",
      done: sources.total > 0,
      href: "/sources",
      action: "Add a database",
    },
    {
      title: "Pick a table to read",
      detail:
        "Find the tables in that database and choose one. Only the tables you pick are ever read.",
      done: ingestion.stream_count > 0,
      href: "/sources",
      action: "Choose a table",
    },
    {
      title: "Run a sync",
      detail:
        "Reads the table and saves what it says. Nothing appears anywhere until this has happened at least once.",
      done: ingestion.observation_count > 0,
      href: "/sources",
      action: "Run it",
    },
    {
      title: "Create an item",
      detail:
        "One real thing your data describes — a product, a shipment, an account.",
      done: ingestion.entity_count > 0,
      href: "/reality",
      action: "Create one",
    },
    {
      title: "Link a row to it",
      detail:
        "Tells RealitySync which row in which table is about that item. Link two databases to the same item and it can start comparing them.",
      done: ingestion.mapped_entity_count > 0,
      href: "/reality",
      action: "Link a row",
    },
    {
      title: "Work out the current values",
      detail:
        "Compares everything your sources said and records the result, including where they disagree.",
      done:
        confidence.scored_state_count + confidence.unscored_attribute_count > 0,
      href: "/reality",
      action: "Recalculate",
    },
  ];
}

export function SetupGuide({ dashboard }: { dashboard: Dashboard }) {
  const steps = setupSteps(dashboard);
  const completed = steps.filter((step) => step.done).length;

  // Nothing left to guide. The panel disappears rather than sitting there
  // congratulating someone every time they load the page.
  if (completed === steps.length) return null;

  // The first unfinished step. Not "the step after the last finished one":
  // setup is not strictly ordered — someone can create an item before syncing
  // — and skipping ahead would point them at a step they cannot complete yet.
  const currentIndex = steps.findIndex((step) => !step.done);

  return (
    <Panel className="border-accent-cyan/30">
      <PanelHeader
        icon={<Rocket />}
        title="Finish setting up"
        description="Six steps to your first answer. Each one is ticked automatically once it is really done."
        action={
          <Badge tone="accent">
            {completed} of {steps.length}
          </Badge>
        }
      />
      <PanelBody className="space-y-4">
        <div
          className="h-1.5 w-full overflow-hidden rounded-full bg-muted"
          role="img"
          aria-label={`${completed} of ${steps.length} steps complete`}
        >
          <div
            className="h-full rounded-full bg-accent-cyan transition-[width] duration-500 ease-out"
            style={{ width: `${(completed / steps.length) * 100}%` }}
          />
        </div>

        <ol className="space-y-1">
          {steps.map((step, index) => {
            const current = index === currentIndex;

            return (
              <li
                key={step.title}
                className={cn(
                  "rounded-lg px-3 py-2.5 transition-colors duration-150",
                  current && "bg-muted/50",
                )}
              >
                <div className="flex items-start gap-3">
                  <span
                    aria-hidden="true"
                    className={cn(
                      "mt-0.5 flex h-6 w-6 shrink-0 items-center justify-center rounded-full border text-xs font-medium",
                      step.done
                        ? "border-status-healthy/30 bg-status-healthy/10 text-status-healthy"
                        : current
                          ? "border-accent-cyan/40 bg-accent-cyan/10 text-accent-cyan"
                          : "border-border bg-muted text-muted-foreground",
                    )}
                  >
                    {step.done ? <Check className="h-3.5 w-3.5" /> : index + 1}
                  </span>

                  <div className="min-w-0 flex-1">
                    <p
                      className={cn(
                        "text-sm",
                        step.done
                          ? "text-muted-foreground line-through decoration-border"
                          : "font-medium text-foreground",
                      )}
                    >
                      {step.title}
                    </p>
                    {/* Only the step in hand explains itself. Six paragraphs
                        at once is the wall of text this replaces. */}
                    {current ? (
                      <p className="mt-1 text-xs leading-relaxed text-muted-foreground">
                        {step.detail}
                      </p>
                    ) : null}
                  </div>

                  {current ? (
                    <Link
                      href={step.href}
                      className={cn(
                        buttonVariants({ variant: "primary", size: "sm" }),
                        "shrink-0",
                      )}
                    >
                      {step.action}
                      <ArrowRight aria-hidden="true" />
                    </Link>
                  ) : null}
                </div>
              </li>
            );
          })}
        </ol>
      </PanelBody>
    </Panel>
  );
}
