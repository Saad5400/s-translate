import { cn } from "@/lib/utils";

export function ShapeDiagram({ id }: { id: string }) {
  if (id === "translated") return <MiniPage rtl />;
  if (id === "original") return <MiniPage />;
  if (id === "stacked")
    return (
      <div className="flex flex-col gap-1 w-full h-full">
        <MiniPage lines={4} className="flex-1" />
        <MiniPage rtl lines={4} className="flex-1" />
      </div>
    );
  if (id === "side-by-side")
    return (
      <div className="flex gap-1 w-full h-full">
        <MiniPage lines={6} className="flex-1" />
        <MiniPage rtl lines={6} className="flex-1" />
      </div>
    );
  return null;
}

function MiniPage({
  rtl,
  lines = 6,
  className,
}: {
  rtl?: boolean;
  lines?: number;
  className?: string;
}) {
  const widths: Array<"short" | "med" | "full"> = [
    "med",
    "full",
    "full",
    "short",
    "full",
    "med",
    "short",
  ];
  return (
    <div
      className={cn(
        "page-mini",
        rtl && "rtl",
        className
      )}
    >
      <div
        className="l"
        style={{
          height: 5,
          width: rtl ? "60%" : "50%",
          marginInlineStart: rtl ? "auto" : 0,
        }}
      />
      {widths.slice(0, lines).map((w, i) => (
        <div
          key={i}
          className="l"
          style={{ width: w === "short" ? "45%" : w === "med" ? "70%" : "100%" }}
        />
      ))}
    </div>
  );
}
