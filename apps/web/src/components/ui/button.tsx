import { cva, type VariantProps } from "class-variance-authority";
import type { ButtonHTMLAttributes } from "react";

import { cn } from "@/lib/utils";

/**
 * Button.
 *
 * `danger` exists so a destructive action can look destructive at the moment it
 * is armed. It is deliberately not used for the resting state of a delete
 * control: a row of red buttons trains people to ignore red.
 *
 * Icons inside a button must carry `aria-hidden`, so the accessible name stays
 * the button's text. The `[&>svg]` rules size them here rather than at every
 * call site.
 */
const buttonVariants = cva(
  [
    "inline-flex items-center justify-center gap-1.5 whitespace-nowrap rounded-md text-sm font-medium",
    "transition-[background-color,border-color,color,opacity,box-shadow] duration-150",
    "focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-ring",
    "disabled:pointer-events-none disabled:opacity-50",
    "[&>svg]:h-4 [&>svg]:w-4 [&>svg]:shrink-0",
  ],
  {
    variants: {
      variant: {
        primary:
          "bg-foreground text-background hover:opacity-90 active:opacity-80",
        secondary:
          "border border-border-strong bg-panel text-foreground hover:border-ring/60 hover:bg-muted",
        ghost: "text-muted-foreground hover:bg-muted hover:text-foreground",
        danger:
          "border border-status-down/40 bg-status-down/10 text-status-down hover:bg-status-down/20",
      },
      size: {
        sm: "h-8 px-3",
        md: "h-9 px-4",
        icon: "h-9 w-9 px-0",
      },
    },
    defaultVariants: { variant: "primary", size: "md" },
  },
);

export type ButtonProps = ButtonHTMLAttributes<HTMLButtonElement> &
  VariantProps<typeof buttonVariants>;

export function Button({ className, variant, size, ...props }: ButtonProps) {
  return (
    <button
      className={cn(buttonVariants({ variant, size }), className)}
      {...props}
    />
  );
}

export { buttonVariants };
