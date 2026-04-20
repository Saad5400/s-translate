import * as React from "react";
import { Pause, Play, X, Clock, ArrowRight, ArrowLeft } from "lucide-react";
import { Button } from "@/components/ui/button";
import { Card } from "@/components/ui/card";
import { Badge } from "@/components/ui/badge";
import { ProgressBar } from "@/components/ui/progress-bar";
import { DocPage } from "@/components/doc-preview";
import type { JobMeta } from "@/lib/api";
import { findLang, STEPS } from "@/lib/data";
import { fmtDur } from "@/lib/utils";

interface LogEntry {
  t: number;          // seconds since start
  level: "info" | "ok" | "warn" | "err";
  msg: string;
}

interface Props {
  job: JobMeta;
  fileName: string;
  logs: LogEntry[];
  onCancel: () => void;
  isRtl: boolean;
}

const LEVEL_TEXT: Record<LogEntry["level"], string> = {
  info: "INFO",
  ok: "OK  ",
  warn: "WARN",
  err: "ERR ",
};

export function ProgressScreen({ job, fileName, logs, onCancel, isRtl }: Props) {
  const [paused, setPaused] = React.useState(false);
  const pct = Math.round(job.progress * 100);

  const msg = job.message || "…";
  // Heuristic: map message to step index
  const stepIndex = React.useMemo(() => {
    const m = msg.toLowerCase();
    if (m.includes("extract")) return 0;
    if (m.includes("chunk") || m.includes("prepar")) return 1;
    if (m.includes("translat") || m.includes("llm") || m.includes("chunk ")) return 2;
    if (m.includes("rtl") || m.includes("mirror")) return 3;
    if (m.includes("combin") || m.includes("reassemb") || m.includes("zip")) return 4;
    if (job.progress >= 0.95) return 4;
    if (job.progress >= 0.85) return 3;
    if (job.progress >= 0.1) return 2;
    return 0;
  }, [msg, job.progress]);

  const revealCount = Math.min(5, Math.max(0, Math.floor(job.progress * 6)));

  const scrollRef = React.useRef<HTMLDivElement>(null);
  React.useEffect(() => {
    if (!paused && scrollRef.current) {
      scrollRef.current.scrollTop = scrollRef.current.scrollHeight;
    }
  }, [logs, paused]);

  const ForwardIcon = isRtl ? ArrowLeft : ArrowRight;

  return (
    <div className="grid gap-6 animate-in fade-in slide-in-from-bottom-1 duration-300">
      <header className="flex items-start justify-between gap-3">
        <div className="grid gap-3">
          <div className="flex items-center gap-2">
            <Badge variant="running">قيد التنفيذ</Badge>
            <span className="font-mono text-[12px] text-paper-3" dir="ltr">
              {job.id}
            </span>
          </div>
          <h1 className="text-[28px] font-bold leading-tight m-0">
            <bdi>{fileName}</bdi>{" "}
            <ForwardIcon className="inline h-5 w-5 text-paper-3" />{" "}
            {findLang(job.target_lang).name}
          </h1>
          <div className="text-paper-2 text-sm truncate">{msg}</div>
        </div>
        <div className="flex items-center gap-2">
          <Button size="sm" onClick={() => setPaused((p) => !p)}>
            {paused ? <Play className="h-3.5 w-3.5" /> : <Pause className="h-3.5 w-3.5" />}
            {paused ? "متابعة العرض" : "إيقاف العرض"}
          </Button>
          <Button size="sm" variant="destructive" onClick={onCancel}>
            <X className="h-3.5 w-3.5" />
            إلغاء
          </Button>
        </div>
      </header>

      <Card className="!p-5">
        <div className="flex items-center justify-between mb-2.5">
          <span className="font-mono text-[13px] text-paper-1">{pct}%</span>
          <span className="text-micro">
            {job.status === "running"
              ? "يُعالج الآن"
              : job.status === "done"
              ? "اكتمل"
              : job.status === "failed"
              ? "فشل"
              : "بالانتظار"}
          </span>
        </div>
        <ProgressBar value={pct} />
      </Card>

      <div className="grid lg:grid-cols-[1fr_380px] gap-5 min-h-0">
        <div className="rounded-lg border border-line bg-ink-1 p-5 grid grid-rows-[auto_1fr_auto] gap-4 min-h-[520px]">
          <div className="flex items-center justify-between gap-3">
            <div>
              <div className="text-micro">معاينة حية</div>
              <div className="text-sm text-paper-1 mt-1">
                يظهر التقدّم من الأصل إلى المُترجَم
              </div>
            </div>
            <div className="flex items-center gap-2">
              <Badge>Before</Badge>
              <ArrowRight className="h-4 w-4 text-paper-4" />
              <Badge variant="running">After · RTL</Badge>
            </div>
          </div>
          <div className="grid grid-cols-2 gap-4 min-h-0">
            <DocPage mode="original" />
            <DocPage mode="translated" revealCount={revealCount} showScan />
          </div>
          <div className="text-micro text-center">
            يتم الحفاظ على الخطوط · الصور · التنسيق — يُعكس الاتجاه للّغات RTL
          </div>
        </div>

        <div className="rounded-lg border border-line bg-ink-1 p-5 grid grid-rows-[auto_auto_1fr] gap-3 min-h-0 min-w-0">
          <div className="flex items-center justify-between">
            <span className="text-micro">سجل مباشر</span>
            <span className="font-mono text-[11px] text-paper-4">stderr · follow</span>
          </div>

          <div className="grid gap-0.5">
            {STEPS.map((s, i) => {
              const done = i < stepIndex || job.status === "done";
              const active = i === stepIndex && job.status === "running";
              return (
                <div
                  key={s.id}
                  className="grid grid-cols-[24px_1fr_auto] gap-2.5 items-center px-0.5 py-2 text-[13px] border-b border-line-soft last:border-b-0"
                  style={{
                    color: done || active
                      ? "var(--color-paper-0)"
                      : "var(--color-paper-2)",
                  }}
                >
                  <span
                    className={
                      "w-[18px] h-[18px] rounded-full grid place-items-center text-[10px] font-mono " +
                      (done
                        ? "bg-accent text-ink-0 border border-accent"
                        : active
                        ? "text-accent border border-accent animate-pulse"
                        : "bg-ink-1 text-paper-3 border border-white/15")
                    }
                  >
                    {done ? "✓" : i + 1}
                  </span>
                  <span>{s.label}</span>
                  <span className="text-paper-4 font-mono text-[11px]">
                    {done ? "تم" : active ? "الآن…" : "—"}
                  </span>
                </div>
              );
            })}
          </div>

          <div
            ref={scrollRef}
            dir="ltr"
            className="overflow-y-auto max-h-[58vh] font-mono text-[12px] leading-[1.7] text-paper-1 text-left pe-1"
          >
            {logs.length === 0 && (
              <div className="text-paper-4">waiting for events…</div>
            )}
            {logs.map((e, i) => (
              <div
                key={i}
                className={
                  "grid grid-cols-[72px_auto_1fr] gap-3 py-0.5 " +
                  (i < logs.length - 4 ? "text-paper-3" : "")
                }
              >
                <span className="text-paper-4">
                  [{e.t.toFixed(1).padStart(5, "0")}s]
                </span>
                <span
                  className={
                    e.level === "ok"
                      ? "text-accent"
                      : e.level === "warn"
                      ? "text-warn"
                      : e.level === "err"
                      ? "text-danger"
                      : "text-paper-2"
                  }
                >
                  {LEVEL_TEXT[e.level]}
                </span>
                <span>{e.msg}</span>
              </div>
            ))}
          </div>
        </div>
      </div>

      <Card className="!p-3.5 flex gap-3 items-center">
        <Clock className="h-4 w-4 text-paper-3" />
        <span className="text-paper-2 text-[13px]">
          يمكنك إغلاق النافذة. ستُتاح النتيجة عبر المُعرِّف{" "}
          <span className="font-mono text-paper-0 mx-1.5" dir="ltr">
            {job.id}
          </span>
          لمدة ٧ أيام.
        </span>
        <span className="ms-auto text-micro">
          مُنقضٍ {fmtDur((Date.now() - job.created_at * 1000) / 1000)}
        </span>
      </Card>
    </div>
  );
}

export type { LogEntry };
