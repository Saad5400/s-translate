import { cn } from "@/lib/utils";

export function FileIcon({
  ext,
  className,
}: {
  ext: string;
  className?: string;
}) {
  return (
    <div
      className={cn(
        "relative w-10 h-12 grid place-items-center rounded-[4px]",
        "bg-ink-3 text-paper-1 border border-line",
        "font-mono text-[10px] font-bold tracking-[0.05em]",
        className
      )}
    >
      {ext.toUpperCase()}
      <span
        className="absolute top-0 end-0 w-2.5 h-2.5"
        style={{
          background:
            "linear-gradient(225deg, oklch(0.28 0.006 250) 50%, oklch(0.26 0.007 250) 50%)",
        }}
        aria-hidden
      />
    </div>
  );
}
