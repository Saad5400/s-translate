import * as React from "react";
import { cn } from "@/lib/utils";

export type SegOption<T extends string> = {
  value: T;
  label: React.ReactNode;
  icon?: React.ReactNode;
};

export function SegControl<T extends string>({
  value,
  onChange,
  options,
  className,
}: {
  value: T;
  onChange: (v: T) => void;
  options: SegOption<T>[];
  className?: string;
}) {
  return (
    <div
      role="tablist"
      className={cn(
        "inline-flex gap-0.5 p-1 rounded-md bg-ink-1 border border-line",
        className
      )}
    >
      {options.map((o) => (
        <button
          key={o.value}
          type="button"
          role="tab"
          aria-selected={value === o.value}
          onClick={() => onChange(o.value)}
          className={cn(
            "inline-flex items-center gap-1.5 h-8 px-3 rounded-sm text-[13px] text-paper-2 transition-colors",
            "hover:text-paper-0",
            "focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-accent-line",
            value === o.value &&
              "bg-ink-3 text-paper-0 shadow-[0_1px_0_oklch(1_0_0/0.06)_inset]"
          )}
        >
          {o.icon}
          {o.label}
        </button>
      ))}
    </div>
  );
}
