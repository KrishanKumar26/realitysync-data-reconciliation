import { Eye, EyeOff } from "lucide-react";
import {
  forwardRef,
  useId,
  useState,
  type InputHTMLAttributes,
  type ReactNode,
} from "react";

import { cn } from "@/lib/utils";

/**
 * Labelled form field.
 *
 * The label is always a real `<label>` bound to the input, and errors are
 * wired through `aria-describedby` and `aria-invalid`. Placeholder-as-label is
 * not used anywhere: it disappears the moment someone starts typing, which is
 * precisely when they most need to know what the field is.
 */

export const Input = forwardRef<
  HTMLInputElement,
  InputHTMLAttributes<HTMLInputElement>
>(function Input({ className, ...props }, ref) {
  return (
    <input
      ref={ref}
      className={cn(
        "h-10 w-full rounded-md border border-border-strong bg-background px-3 text-sm",
        "text-foreground placeholder:text-muted-foreground",
        "transition-[border-color,box-shadow] duration-150",
        "focus-visible:border-ring focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring/35",
        "disabled:cursor-not-allowed disabled:opacity-60",
        "aria-[invalid=true]:border-status-down aria-[invalid=true]:ring-status-down/25",
        className,
      )}
      {...props}
    />
  );
});

export function Field({
  label,
  hint,
  error,
  children,
  className,
}: {
  label: string;
  hint?: string;
  error?: string | null;
  /** Receives the generated ids so the control stays accessible. */
  children: (ids: {
    inputId: string;
    describedBy: string | undefined;
  }) => ReactNode;
  className?: string;
}) {
  const inputId = useId();
  const hintId = `${inputId}-hint`;
  const errorId = `${inputId}-error`;

  const describedBy =
    [hint ? hintId : null, error ? errorId : null].filter(Boolean).join(" ") ||
    undefined;

  return (
    <div className={cn("space-y-1.5", className)}>
      <label
        htmlFor={inputId}
        className="block text-sm font-medium text-foreground"
      >
        {label}
      </label>

      {children({ inputId, describedBy })}

      {hint && !error ? (
        <p id={hintId} className="text-xs text-muted-foreground">
          {hint}
        </p>
      ) : null}

      {error ? (
        <p id={errorId} className="text-xs text-status-down">
          {error}
        </p>
      ) : null}
    </div>
  );
}

/**
 * Password input with a reveal control.
 *
 * A password box that cannot be read back is a good default and a bad only
 * option: people mistype, and on a phone with autocorrect they mistype often.
 * The alternative — retyping blind after every failed sign-in — is what makes
 * someone reset a password they already knew.
 *
 * The toggle is a `<button type="button">` so it never submits the form, and
 * its accessible name changes with the state, so a screen reader announces
 * what pressing it will do rather than a static "toggle".
 *
 * `autoComplete` is left to the caller: "current-password" and
 * "new-password" mean different things to a password manager, and guessing
 * here would fight it.
 */
export const PasswordInput = forwardRef<
  HTMLInputElement,
  Omit<InputHTMLAttributes<HTMLInputElement>, "type">
>(function PasswordInput({ className, ...props }, ref) {
  const [visible, setVisible] = useState(false);

  return (
    <div className="relative">
      <Input
        ref={ref}
        type={visible ? "text" : "password"}
        className={cn("pr-10", className)}
        {...props}
      />
      <button
        type="button"
        onClick={() => setVisible((shown) => !shown)}
        aria-pressed={visible}
        aria-label={visible ? "Hide password" : "Show password"}
        title={visible ? "Hide password" : "Show password"}
        className={cn(
          "absolute right-0 top-0 flex h-10 w-10 items-center justify-center rounded-md",
          "text-muted-foreground transition-colors duration-150 hover:text-foreground",
          "focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-ring",
        )}
      >
        {visible ? (
          <EyeOff className="h-4 w-4" aria-hidden="true" />
        ) : (
          <Eye className="h-4 w-4" aria-hidden="true" />
        )}
      </button>
    </div>
  );
});
