import * as React from "react";
import * as ToastPrimitive from "@radix-ui/react-toast";
import { AlertTriangle, CheckCircle2, Info, XCircle, X } from "lucide-react";
import { cn } from "@/lib/utils";

export type ToastVariant = "info" | "ok" | "warn" | "error";

interface ToastItem {
  id: string;
  title: string;
  description?: React.ReactNode;
  variant?: ToastVariant;
}

interface ToastCtx {
  toast: (t: Omit<ToastItem, "id">) => void;
}

const Ctx = React.createContext<ToastCtx | null>(null);

export function useToast(): ToastCtx {
  const ctx = React.useContext(Ctx);
  if (!ctx) throw new Error("useToast must be inside <ToastProvider>");
  return ctx;
}

const ICONS: Record<ToastVariant, React.ReactNode> = {
  info: <Info className="h-4 w-4" />,
  ok: <CheckCircle2 className="h-4 w-4" />,
  warn: <AlertTriangle className="h-4 w-4" />,
  error: <XCircle className="h-4 w-4" />,
};

export function ToastProvider({ children }: { children: React.ReactNode }) {
  const [items, setItems] = React.useState<ToastItem[]>([]);

  const toast = React.useCallback((t: Omit<ToastItem, "id">) => {
    const id = Math.random().toString(36).slice(2);
    setItems((prev) => [...prev, { id, ...t }]);
  }, []);

  return (
    <Ctx.Provider value={{ toast }}>
      <ToastPrimitive.Provider swipeDirection="right" duration={5000}>
        {children}
        {items.map((it) => (
          <ToastPrimitive.Root
            key={it.id}
            onOpenChange={(open) => {
              if (!open) setItems((prev) => prev.filter((x) => x.id !== it.id));
            }}
            className={cn(
              "group pointer-events-auto relative flex w-[360px] items-start gap-3 rounded-md border px-4 py-3 shadow-[0_1px_0_oklch(1_0_0/0.04)_inset,_0_10px_30px_oklch(0_0_0/0.35)]",
              "data-[state=open]:animate-in data-[state=closed]:animate-out data-[state=closed]:fade-out-0 data-[state=open]:slide-in-from-bottom-2",
              it.variant === "error" && "bg-[oklch(0.72_0.17_28/0.08)] border-[oklch(0.72_0.17_28/0.4)]",
              it.variant === "warn" && "bg-ink-2 border-[oklch(0.82_0.13_82/0.4)]",
              it.variant === "ok" && "bg-ink-2 border-accent-line",
              (!it.variant || it.variant === "info") && "bg-ink-2 border-line-strong"
            )}
          >
            <span
              className={cn(
                "mt-0.5 shrink-0",
                it.variant === "error" && "text-danger",
                it.variant === "warn" && "text-warn",
                it.variant === "ok" && "text-accent",
                (!it.variant || it.variant === "info") && "text-paper-2"
              )}
            >
              {ICONS[it.variant ?? "info"]}
            </span>
            <div className="flex-1 min-w-0 grid gap-0.5">
              <ToastPrimitive.Title className="text-[13px] font-semibold leading-snug text-paper-0">
                {it.title}
              </ToastPrimitive.Title>
              {it.description && (
                <ToastPrimitive.Description className="text-[12px] text-paper-2 leading-snug break-words">
                  {it.description}
                </ToastPrimitive.Description>
              )}
            </div>
            <ToastPrimitive.Close
              aria-label="إغلاق"
              className="text-paper-3 hover:text-paper-0 transition-colors p-1 -m-1"
            >
              <X className="h-3.5 w-3.5" />
            </ToastPrimitive.Close>
          </ToastPrimitive.Root>
        ))}
        <ToastPrimitive.Viewport className="fixed bottom-5 start-5 z-[200] flex flex-col gap-2 outline-none" />
      </ToastPrimitive.Provider>
    </Ctx.Provider>
  );
}
