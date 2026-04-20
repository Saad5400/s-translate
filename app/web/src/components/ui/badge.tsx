import * as React from "react";
import { cva, type VariantProps } from "class-variance-authority";
import { cn } from "@/lib/utils";

const badgeVariants = cva(
  "inline-flex items-center gap-1.5 px-2.5 py-[3px] rounded-full text-[11px] font-mono tracking-[0.04em] uppercase border whitespace-nowrap",
  {
    variants: {
      variant: {
        default: "bg-ink-2 text-paper-1 border-line",
        running:
          "bg-accent-soft text-accent border-accent-line",
        done: "bg-ink-2 text-paper-1 border-line",
        failed:
          "bg-[oklch(0.72_0.17_28/0.12)] text-danger border-[oklch(0.72_0.17_28/0.35)]",
        queued: "bg-ink-2 text-paper-2 border-line",
      },
    },
    defaultVariants: { variant: "default" },
  }
);

export interface BadgeProps
  extends React.HTMLAttributes<HTMLSpanElement>,
    VariantProps<typeof badgeVariants> {
  dot?: boolean;
}

export function Badge({ className, variant, dot = true, children, ...props }: BadgeProps) {
  return (
    <span className={cn(badgeVariants({ variant }), className)} {...props}>
      {dot && (
        <span
          className={cn(
            "w-1.5 h-1.5 rounded-full",
            variant === "running" && "bg-accent animate-pulse",
            variant === "done" && "bg-accent",
            variant === "failed" && "bg-danger",
            (variant === "default" || variant === "queued" || !variant) &&
              "bg-paper-3"
          )}
        />
      )}
      {children}
    </span>
  );
}
