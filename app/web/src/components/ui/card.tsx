import * as React from "react";
import { cn } from "@/lib/utils";

export const Card = React.forwardRef<
  HTMLDivElement,
  React.HTMLAttributes<HTMLDivElement>
>(({ className, ...props }, ref) => (
  <div
    ref={ref}
    className={cn(
      "rounded-lg border border-line p-5",
      "bg-gradient-to-b from-[oklch(0.18_0.005_250/0.92)] to-[oklch(0.15_0.005_250/0.92)]",
      className
    )}
    {...props}
  />
));
Card.displayName = "Card";

export const CardHead = React.forwardRef<
  HTMLDivElement,
  React.HTMLAttributes<HTMLDivElement>
>(({ className, ...props }, ref) => (
  <div
    ref={ref}
    className={cn(
      "flex items-center justify-between gap-3 mb-4",
      className
    )}
    {...props}
  />
));
CardHead.displayName = "CardHead";

export const CardTitle = React.forwardRef<
  HTMLHeadingElement,
  React.HTMLAttributes<HTMLHeadingElement>
>(({ className, ...props }, ref) => (
  <h3
    ref={ref}
    className={cn(
      "text-[14px] font-mono font-medium tracking-[0.08em] uppercase text-paper-2 m-0 whitespace-nowrap",
      className
    )}
    {...props}
  />
));
CardTitle.displayName = "CardTitle";
