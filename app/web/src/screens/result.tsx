import * as React from "react";
import { Copy, Check, Download, Plus, Settings, ChevronLeft, ChevronRight, AlertTriangle } from "lucide-react";
import { Button } from "@/components/ui/button";
import { Card, CardHead, CardTitle } from "@/components/ui/card";
import { Badge } from "@/components/ui/badge";
import { SegControl } from "@/components/ui/seg-control";
import { DocPage } from "@/components/doc-preview";
import { downloadUrl, type JobMeta } from "@/lib/api";
import { findLang, findProvider, findShape } from "@/lib/data";
import { fmtDur } from "@/lib/utils";

interface Props {
  job: JobMeta;
  onAnother: () => void;
  onOpenSettings: () => void;
}

export function ResultScreen({ job, onAnother, onOpenSettings }: Props) {
  const tgt = findLang(job.target_lang);
  const shape =
    job.output_mode === "both_horizontal"
      ? findShape("side-by-side")
      : job.output_mode === "both_vertical"
      ? findShape("stacked")
      : job.output_mode === "original"
      ? findShape("original")
      : findShape("translated");

  const [view, setView] = React.useState<"translated" | "original" | "side-by-side">(
    shape.id === "side-by-side" ? "side-by-side" : "translated"
  );
  const [copied, setCopied] = React.useState(false);

  const isDone = job.status === "done";
  const isFailed = job.status === "failed";
  const outName =
    job.output_name ||
    (job.input_name
      ? job.input_name.replace(/\.[^.]+$/, `.${tgt.code}$&`)
      : `${job.id}.out`);

  function copyLink() {
    const url = new URL(window.location.href);
    url.hash = `job=${job.id}`;
    navigator.clipboard?.writeText(url.toString()).catch(() => {});
    setCopied(true);
    setTimeout(() => setCopied(false), 1500);
  }

  return (
    <div className="grid gap-6 animate-in fade-in slide-in-from-bottom-1 duration-300">
      <header className="flex items-start justify-between gap-3">
        <div className="grid gap-3 min-w-0">
          <div className="flex items-center gap-2">
            <Badge variant={isFailed ? "failed" : "done"}>
              {isFailed ? "فشل" : "اكتملت"}
            </Badge>
            <span className="font-mono text-[12px] text-paper-3" dir="ltr">
              {job.id}
            </span>
          </div>
          <h1 className="text-[28px] font-bold leading-tight m-0 truncate" dir="ltr">
            <bdi>{outName}</bdi>
          </h1>
          <div className="text-paper-2 text-sm">
            تُرجم إلى {tgt.name} · {shape.label} · المدة{" "}
            {fmtDur(Math.max(1, (job.updated_at - job.created_at) || 1))}
          </div>
        </div>
        <div className="flex items-center gap-2 shrink-0">
          <Button onClick={copyLink}>
            {copied ? <Check className="h-4 w-4" /> : <Copy className="h-4 w-4" />}
            {copied ? "نُسخ" : "نسخ الرابط"}
          </Button>
          {isDone && (
            <Button variant="accent" asChild>
              <a href={downloadUrl(job.id)} download>
                <Download className="h-4 w-4" />
                تنزيل
              </a>
            </Button>
          )}
        </div>
      </header>

      {isFailed && (
        <div className="p-4 rounded-md border border-[oklch(0.72_0.17_28/0.35)] bg-[oklch(0.72_0.17_28/0.08)] flex gap-3 items-start">
          <AlertTriangle className="h-5 w-5 text-danger shrink-0 mt-0.5" />
          <div>
            <div className="font-semibold text-paper-0">
              تعذّر إكمال الترجمة
            </div>
            <div className="text-[13px] text-paper-2 mt-0.5 break-words">
              {job.error || "سبب غير معروف."}
            </div>
          </div>
        </div>
      )}

      <div className="grid lg:grid-cols-[1fr_320px] gap-5">
        <div className="rounded-lg border border-line bg-ink-1 p-5 grid gap-4 min-w-0">
          <div className="flex items-center justify-between gap-3">
            <div className="text-micro">المعاينة</div>
            <SegControl
              value={view}
              onChange={setView}
              options={[
                { value: "translated", label: "المُترجَم" },
                { value: "original", label: "الأصل" },
                { value: "side-by-side", label: "جنبًا إلى جنب" },
              ]}
            />
          </div>
          <div className="rounded-md border border-line bg-ink-0 bg-grid-paper p-6 min-h-[60vh] grid place-items-center">
            {view === "translated" && (
              <div className="w-[min(520px,100%)]">
                <DocPage mode="translated" />
              </div>
            )}
            {view === "original" && (
              <div className="w-[min(520px,100%)]">
                <DocPage mode="original" />
              </div>
            )}
            {view === "side-by-side" && (
              <div className="w-[min(900px,100%)] grid grid-cols-2 gap-3.5">
                <DocPage mode="original" />
                <DocPage mode="translated" />
              </div>
            )}
          </div>
          <div className="flex items-center justify-between flex-wrap gap-3">
            <div className="flex items-center gap-2 text-paper-3 text-[12px]">
              <span className="font-mono">Page 1</span>
              <span>·</span>
              <span>الخطوط والأنماط محفوظة</span>
              {tgt.rtl && (
                <>
                  <span>·</span>
                  <span className="text-accent">تم عكس التخطيط</span>
                </>
              )}
            </div>
            <div className="flex items-center gap-1.5">
              <Button variant="ghost" size="iconSm" aria-label="السابق">
                <ChevronRight className="h-3.5 w-3.5" />
              </Button>
              <Button variant="ghost" size="iconSm" aria-label="التالي">
                <ChevronLeft className="h-3.5 w-3.5" />
              </Button>
            </div>
          </div>
        </div>

        <div className="grid gap-4 min-w-0">
          <Card>
            <CardHead>
              <CardTitle>تفاصيل الطلبية</CardTitle>
            </CardHead>
            <div>
              <Kv k="المُعرِّف" v={<span className="font-mono" dir="ltr">{job.id}</span>} />
              <Kv k="الملف" v={<span title={job.input_name}>{job.input_name}</span>} />
              <Kv k="اللغة" v={<span dir="ltr">{tgt.english}</span>} />
              <Kv k="الشكل" v={shape.label} />
              <Kv k="المزوِّد" v={findProvider(job.provider).name} />
              <Kv
                k="النموذج"
                v={
                  <span className="font-mono text-[12px]" dir="ltr">
                    {job.model}
                  </span>
                }
              />
              {job.output_name && (
                <Kv
                  k="الناتج"
                  v={
                    <span className="font-mono text-[12px]" dir="ltr">
                      {job.output_name}
                    </span>
                  }
                />
              )}
              <Kv
                k="المدة"
                v={<bdi>{fmtDur(Math.max(1, (job.updated_at - job.created_at) || 1))}</bdi>}
              />
              <Kv k="ينتهي" v="بعد ٧ أيام" />
            </div>
          </Card>

          <Card>
            <CardHead>
              <CardTitle>مشاركة / استعادة</CardTitle>
            </CardHead>
            <div className="flex items-center gap-2 border border-line rounded-md px-3 py-2 bg-ink-1 font-mono text-[13px]" dir="ltr">
              <span className="text-paper-3">ID</span>
              <span className="flex-1 truncate text-paper-0">{job.id}</span>
              <Button variant="ghost" size="sm" onClick={copyLink}>
                {copied ? <Check className="h-3.5 w-3.5" /> : <Copy className="h-3.5 w-3.5" />}
                {copied ? "نُسخ" : "نسخ"}
              </Button>
            </div>
            <div className="text-paper-2 text-[12px] mt-2.5">
              افتح هذا المُعرِّف من أي جهاز لتنزيل النتيجة خلال ٧ أيام.
            </div>
          </Card>

          <div className="flex gap-2 flex-wrap">
            <Button className="flex-1" onClick={onAnother}>
              <Plus className="h-4 w-4" />
              ترجمة جديدة
            </Button>
            <Button variant="ghost" className="flex-1" onClick={onOpenSettings}>
              <Settings className="h-4 w-4" />
              الإعدادات
            </Button>
          </div>
        </div>
      </div>
    </div>
  );
}

function Kv({ k, v }: { k: string; v: React.ReactNode }) {
  return (
    <div className="grid grid-cols-[auto_1fr] gap-3 py-2.5 border-b border-line-soft last:border-b-0 text-[13px] items-baseline">
      <span className="text-[11px] text-paper-3 font-mono tracking-[0.06em] uppercase whitespace-nowrap">
        {k}
      </span>
      <span className="text-paper-0 font-medium text-end min-w-0 overflow-hidden text-ellipsis whitespace-nowrap">
        {v}
      </span>
    </div>
  );
}
