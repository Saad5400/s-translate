import * as React from "react";
import { cn } from "@/lib/utils";

export type InputProps = React.InputHTMLAttributes<HTMLInputElement>;

export const Input = React.forwardRef<HTMLInputElement, InputProps>(
  ({ className, type = "text", ...props }, ref) => (
    <input
      ref={ref}
      type={type}
      className={cn(
        "h-11 w-full rounded-md bg-ink-1 px-4 text-paper-0 text-[15px] font-ar",
        "border border-line hover:border-line-strong",
        "focus-visible:outline-none focus-visible:border-accent-line focus-visible:ring-2 focus-visible:ring-accent-soft focus-visible:bg-ink-2",
        "placeholder:text-paper-3",
        "disabled:opacity-50 disabled:cursor-not-allowed",
        "transition-colors",
        className
      )}
      {...props}
    />
  )
);
Input.displayName = "Input";

export const Textarea = React.forwardRef<
  HTMLTextAreaElement,
  React.TextareaHTMLAttributes<HTMLTextAreaElement>
>(({ className, ...props }, ref) => (
  <textarea
    ref={ref}
    className={cn(
      "min-h-24 w-full rounded-md bg-ink-1 px-4 py-3 text-paper-0 text-[15px] font-ar leading-relaxed",
      "border border-line hover:border-line-strong",
      "focus-visible:outline-none focus-visible:border-accent-line focus-visible:ring-2 focus-visible:ring-accent-soft focus-visible:bg-ink-2",
      "placeholder:text-paper-3 resize-y",
      "disabled:opacity-50",
      "transition-colors",
      className
    )}
    {...props}
  />
));
Textarea.displayName = "Textarea";
