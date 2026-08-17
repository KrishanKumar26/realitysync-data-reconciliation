"use client";

import { useEffect, useRef, useState } from "react";

import { Button } from "@/components/ui/button";

/**
 * Two-step destructive action.
 *
 * Deleting a source was a single unguarded click that removed the source, its
 * stored credentials, its tables and every record ever read from it, with no
 * confirmation and no undo. The button is now armed first and must be pressed
 * again to fire, and it disarms itself after a few seconds so a forgotten
 * half-press cannot sit there waiting to be triggered by a mis-click later.
 *
 * A two-step button rather than a modal: a modal for one destructive action on
 * one screen is more machinery — focus trapping, escape handling, scroll
 * locking — than the decision warrants, and each of those is a thing to get
 * subtly wrong.
 */
export function ConfirmAction({
  label,
  confirmLabel,
  pendingLabel,
  onConfirm,
  disabled = false,
  size = "sm",
}: {
  label: string;
  confirmLabel: string;
  pendingLabel?: string;
  onConfirm: () => void | Promise<void>;
  disabled?: boolean;
  size?: "sm" | "md";
}) {
  const [armed, setArmed] = useState(false);
  const [pending, setPending] = useState(false);
  const timer = useRef<ReturnType<typeof setTimeout> | null>(null);

  useEffect(() => {
    return () => {
      if (timer.current) clearTimeout(timer.current);
    };
  }, []);

  function disarmLater() {
    if (timer.current) clearTimeout(timer.current);
    timer.current = setTimeout(() => setArmed(false), 5000);
  }

  async function handleClick() {
    if (!armed) {
      setArmed(true);
      disarmLater();
      return;
    }
    if (timer.current) clearTimeout(timer.current);
    setPending(true);
    try {
      await onConfirm();
    } finally {
      setPending(false);
      setArmed(false);
    }
  }

  return (
    <div className="flex items-center gap-2">
      {armed && !pending ? (
        <Button
          type="button"
          variant="ghost"
          size={size}
          onClick={() => {
            if (timer.current) clearTimeout(timer.current);
            setArmed(false);
          }}
        >
          Cancel
        </Button>
      ) : null}
      <Button
        type="button"
        variant={armed ? "danger" : "secondary"}
        size={size}
        disabled={disabled || pending}
        onClick={() => void handleClick()}
      >
        {pending ? (pendingLabel ?? "Working…") : armed ? confirmLabel : label}
      </Button>
    </div>
  );
}
