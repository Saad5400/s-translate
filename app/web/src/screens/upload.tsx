import * as React from "react";
import { ArrowLeft, ArrowRight, Upload as UploadIcon, X, Settings as SettingsIcon } from "lucide-react";
import { Button } from "@/components/ui/button";
import { Card, CardHead, CardTitle } from "@/components/ui/card";
import { Badge } from "@/components/ui/badge";
import { Input } from "@/components/ui/input";
import { FileIcon } from "@/components/file-icon";
import { ACCEPT_EXTENSIONS, FILE_TYPES } from "@/lib/data";
import { fmtSize } from "@/lib/utils";
import { cn } from "@/lib/utils";

export interface StagedFile {
  file: File;
  name: string;
  ext: string;
  sizeKb: number;
}

interface Props {
  stagedFiles: StagedFile[];
  onAdd: (f: StagedFile) => void;
  onRemove: (idx: number) => void;
  onContinue: () => void;
  onResumeById: (id: string) => void;
  configured: boolean;
  onOpenSettings: () => void;
  isRtl: boolean;
}

export function UploadScreen({
  stagedFiles,
  onAdd,
  onRemove,
  onContinue,
  onResumeById,
  configured,
  onOpenSettings,
  isRtl,
}: Props) {
  const [dragOver, setDragOver] = React.useState(false);
  const [idInput, setIdInput] = React.useState("");
  const inputRef = React.useRef<HTMLInputElement>(null);

  function handleFiles(list: FileList | null) {
    if (!list) return;
    for (const f of Array.from(list)) {
      const ext = (f.name.split(".").pop() || "").toUpperCase();
      onAdd({
        file: f,
        name: f.name,
        ext,
        sizeKb: Math.max(1, Math.round(f.size / 1024)),
      });
    }
  }

  const hasFiles = stagedFiles.length > 0;
  const locked = !configured;
  const ForwardIcon = isRtl ? ArrowLeft : ArrowRight;

  return (
    <div className="grid gap-8 animate-in fade-in slide-in-from-bottom-1 duration-300">
      <header className="grid gap-4">
        <div className="text-eyebrow">ابدأ ترجمة جديدة</div>
        <h1 className="text-[44px] font-bold leading-[1.08] tracking-[-0.01em] m-0">
          أرسل مستندًا، احتفظ بتنسيقه.
        </h1>
        <p className="text-[16px] text-paper-2 max-w-[56ch] m-0 text-pretty">
          يدعم Word و PowerPoint و Excel و PDF والنصوص العادية. يُحافظ على الخطوط
          والجداول والصور والألوان — ويعكس التخطيط بالكامل عند الترجمة إلى لغة
          تُكتب من اليمين إلى اليسار.
        </p>
      </header>

      {locked && (
        <div className="flex items-start gap-3 p-4 rounded-md border border-[oklch(0.82_0.13_82/0.3)] bg-[oklch(0.82_0.13_82/0.08)]">
          <SettingsIcon className="h-5 w-5 shrink-0 text-warn mt-0.5" />
          <div className="flex-1 min-w-0">
            <div className="font-semibold text-paper-0">
              أكمل إعدادات الذكاء الاصطناعي أولًا
            </div>
            <div className="text-[13px] text-paper-2 mt-0.5">
              حدّد المزوِّد ومفتاح الـ API والنموذج قبل بدء أول ترجمة. تبقى بياناتك
              في هذا المتصفح فقط.
            </div>
          </div>
          <Button onClick={onOpenSettings} variant="accent" className="shrink-0">
            <SettingsIcon className="h-4 w-4" />
            فتح الإعدادات
          </Button>
        </div>
      )}

      {!hasFiles ? (
        <div
          onClick={() => !locked && inputRef.current?.click()}
          onDragOver={(e) => { e.preventDefault(); if (!locked) setDragOver(true); }}
          onDragLeave={() => setDragOver(false)}
          onDrop={(e) => {
            e.preventDefault();
            setDragOver(false);
            if (!locked) handleFiles(e.dataTransfer.files);
          }}
          role="button"
          tabIndex={locked ? -1 : 0}
          aria-disabled={locked}
          onKeyDown={(e) => {
            if (locked) return;
            if (e.key === "Enter" || e.key === " ") {
              e.preventDefault();
              inputRef.current?.click();
            }
          }}
          className={cn(
            "relative grid place-items-center text-center cursor-pointer rounded-[20px] min-h-[360px] p-16 transition-colors",
            "border-[1.5px] border-dashed border-white/10",
            "bg-[radial-gradient(600px_300px_at_50%_100%,var(--color-accent-soft),transparent_70%),linear-gradient(180deg,oklch(0.18_0.005_250/0.6),oklch(0.14_0.005_250/0.6))]",
            "hover:border-accent-line",
            dragOver && "border-accent bg-[radial-gradient(600px_300px_at_50%_100%,var(--color-accent-soft),transparent_70%),oklch(0.18_0.005_155/0.4)]",
            locked && "opacity-50 cursor-not-allowed pointer-events-none"
          )}
        >
          <input
            ref={inputRef}
            type="file"
            multiple
            accept={ACCEPT_EXTENSIONS}
            onChange={(e) => { handleFiles(e.target.files); e.target.value = ""; }}
            className="sr-only"
            aria-label="اختر ملفًا"
          />
          <div className="grid place-items-center w-24 h-24 rounded-[28px] border border-white/10 bg-white/[0.03] mb-5 text-paper-1">
            <UploadIcon className="h-9 w-9" strokeWidth={1.4} />
          </div>
          <div className="text-[28px] font-bold leading-tight">اسحب الملفات إلى هنا</div>
          <p className="text-paper-2 mt-2 m-0">
            أو{" "}
            <span className="text-accent underline underline-offset-2">
              اختر من جهازك
            </span>{" "}
            — الحجم الأقصى ٥٠ ميجابايت.
          </p>
          <div className="mt-5 flex flex-wrap gap-2 justify-center">
            {FILE_TYPES.map((ft) => (
              <Badge key={ft.ext}>{ft.ext}</Badge>
            ))}
          </div>
        </div>
      ) : (
        <Card
          onDragOver={(e) => { e.preventDefault(); setDragOver(true); }}
          onDragLeave={() => setDragOver(false)}
          onDrop={(e) => {
            e.preventDefault();
            setDragOver(false);
            handleFiles(e.dataTransfer.files);
          }}
          className={cn(
            "transition-colors",
            dragOver && "border-accent ring-2 ring-accent-soft"
          )}
        >
          <CardHead>
            <CardTitle>في طابور الترجمة · {stagedFiles.length}</CardTitle>
            <Button variant="ghost" size="sm" onClick={() => inputRef.current?.click()}>
              + إضافة المزيد
            </Button>
            <input
              ref={inputRef}
              type="file"
              multiple
              accept={ACCEPT_EXTENSIONS}
              onChange={(e) => { handleFiles(e.target.files); e.target.value = ""; }}
              className="sr-only"
            />
          </CardHead>
          <div className="grid gap-3">
            {stagedFiles.map((f, i) => (
              <div
                key={i}
                className="grid grid-cols-[40px_1fr_auto] items-center gap-4 px-4 py-3 rounded-md border border-line bg-ink-1"
              >
                <FileIcon ext={f.ext} />
                <div className="min-w-0">
                  <div className="font-medium text-paper-0 truncate">{f.name}</div>
                  <div className="text-[12px] text-paper-3 font-mono tracking-[0.02em]">
                    {fmtSize(f.sizeKb)}
                  </div>
                </div>
                <Button
                  variant="ghost"
                  size="iconSm"
                  aria-label="إزالة"
                  onClick={() => onRemove(i)}
                >
                  <X className="h-4 w-4" />
                </Button>
              </div>
            ))}
          </div>
          <div className="flex items-center justify-between mt-5">
            <div className="text-micro">الخطوة ١ من ٣ · الرفع</div>
            <Button variant="accent" onClick={onContinue} disabled={locked}>
              إعداد الترجمة
              <ForwardIcon className="h-4 w-4" />
            </Button>
          </div>
        </Card>
      )}

      <div className="grid md:grid-cols-2 gap-4">
        <Card>
          <CardHead>
            <CardTitle>استئناف بواسطة المُعرِّف</CardTitle>
          </CardHead>
          <p className="text-paper-2 text-[13px] m-0">
            لديك مُعرِّف ترجمة من تبويب سابق؟ ألصقه هنا لاستعادة النتيجة.
          </p>
          <form
            className="flex gap-2 mt-3"
            onSubmit={(e) => {
              e.preventDefault();
              const id = idInput.trim();
              if (id) onResumeById(id);
            }}
          >
            <Input
              placeholder="tr_01HZ…"
              value={idInput}
              onChange={(e) => setIdInput(e.target.value)}
              dir="ltr"
              className="font-mono"
              aria-label="مُعرِّف الطلبية"
            />
            <Button type="submit" disabled={!idInput.trim()}>
              فتح
            </Button>
          </form>
        </Card>

        <Card>
          <CardHead>
            <CardTitle>كيف يعمل</CardTitle>
          </CardHead>
          <ol className="m-0 ps-5 text-paper-1 text-sm leading-[2]">
            <li>ارفع الملفات واختر لغة الهدف وشكل الإخراج.</li>
            <li>أضف بيانات اعتماد مزوِّد الذكاء الاصطناعي (تبقى في المتصفح فقط).</li>
            <li>يعمل التحويل على الخادم — ويمكنك الإغلاق والعودة لاحقًا.</li>
          </ol>
        </Card>
      </div>
    </div>
  );
}
