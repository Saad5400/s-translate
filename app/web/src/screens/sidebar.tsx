import { Plus } from "lucide-react";
import { Button } from "@/components/ui/button";
import { Badge } from "@/components/ui/badge";
import type { JobMeta } from "@/lib/api";
import { findLang } from "@/lib/data";
import { cn } from "@/lib/utils";

interface Props {
  jobs: JobMeta[];
  currentJobId?: string;
  onNewJob: () => void;
  onPickJob: (j: JobMeta) => void;
}

export function Sidebar({ jobs, currentJobId, onNewJob, onPickJob }: Props) {
  const recent = jobs.slice(0, 12);
  return (
    <aside className="hidden md:grid grid-rows-[auto_1fr_auto] min-h-screen max-h-screen sticky top-0 border-e border-line bg-[oklch(0.11_0.005_250)]">
      <div className="flex items-center gap-2.5 px-5 py-4 border-b border-line">
        <div
          className="w-8 h-8 grid place-items-center rounded-md bg-paper-0 text-ink-0 font-black text-[18px]"
          aria-hidden
        >
          س
        </div>
        <div className="min-w-0">
          <div className="text-[16px] font-bold leading-tight">س‑ترجم</div>
          <div className="text-[10px] text-paper-3 font-mono tracking-[0.1em] uppercase">
            s-trans · self-hosted
          </div>
        </div>
      </div>

      <nav className="overflow-y-auto p-3">
        <Button
          variant="accent"
          className="w-full mb-3"
          onClick={onNewJob}
        >
          <Plus className="h-4 w-4" />
          ترجمة جديدة
        </Button>

        <div className="mt-3">
          <div className="flex items-center justify-between px-2.5 py-1.5 text-[10px] text-paper-4 font-mono tracking-[0.12em] uppercase">
            <span>الأعمال الأخيرة</span>
            <span>{jobs.length}</span>
          </div>
          {recent.length === 0 && (
            <div className="px-2.5 py-4 text-[12px] text-paper-3 leading-relaxed">
              لا توجد ترجمات سابقة بعد. ابدأ بترجمة مستند وستظهر هنا.
            </div>
          )}
          <div className="grid gap-1 px-0.5">
            {recent.map((j) => (
              <button
                key={j.id}
                type="button"
                onClick={() => onPickJob(j)}
                aria-current={currentJobId === j.id}
                className={cn(
                  "text-start w-full grid gap-1 p-2.5 rounded-md border border-transparent cursor-pointer",
                  "hover:bg-white/5",
                  currentJobId === j.id && "bg-white/[0.04] border-line"
                )}
              >
                <div className="text-[13px] font-medium text-paper-0 truncate">
                  {j.input_name || "(بدون اسم)"}
                </div>
                <div className="flex items-center gap-2 text-[10.5px] text-paper-3 font-mono tracking-[0.04em] uppercase">
                  <span dir="ltr">{findLang(j.target_lang).english}</span>
                  <span>·</span>
                  <Badge
                    variant={
                      j.status === "done"
                        ? "done"
                        : j.status === "failed"
                        ? "failed"
                        : j.status === "running"
                        ? "running"
                        : "queued"
                    }
                  >
                    {j.status === "done"
                      ? "تم"
                      : j.status === "failed"
                      ? "فشل"
                      : j.status === "running"
                      ? "قيد التنفيذ"
                      : "بالانتظار"}
                  </Badge>
                </div>
              </button>
            ))}
          </div>
        </div>
      </nav>

      <div className="px-4 py-3 border-t border-line grid gap-1.5 text-[12px] text-paper-3">
        <div className="flex items-center justify-between">
          <span className="text-micro">v0.1.0</span>
          <span className="text-micro text-accent">● متصل</span>
        </div>
        <div className="text-[11px] text-paper-4">
          تُحذف الملفات تلقائيًا بعد ٧ أيام.
        </div>
      </div>
    </aside>
  );
}
