import { ArrowLeft, ArrowRight, Sparkles, KeyRound, FlipHorizontal2, Loader2 } from "lucide-react";
import { Button } from "@/components/ui/button";
import { Card, CardHead, CardTitle } from "@/components/ui/card";
import { Badge } from "@/components/ui/badge";
import { Input, Textarea } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Combobox, type ComboboxOption } from "@/components/ui/combobox";
import { SecretField } from "@/components/ui/secret-field";
import { FileIcon } from "@/components/file-icon";
import { ShapeDiagram } from "@/components/shape-diagram";
import {
  LANGUAGES,
  PROVIDERS,
  OUTPUT_SHAPES,
  findLang,
  findProvider,
} from "@/lib/data";
import type { Config } from "@/lib/store";
import type { StagedFile } from "./upload";
import { fmtSize, cn } from "@/lib/utils";

interface Props {
  cfg: Config;
  setCfg: (c: Config) => void;
  stagedFiles: StagedFile[];
  onBack: () => void;
  onStart: () => void;
  isRtl: boolean;
  starting?: boolean;
}

export function ConfigureScreen({ cfg, setCfg, stagedFiles, onBack, onStart, isRtl, starting = false }: Props) {
  const provider = findProvider(cfg.providerId);
  const targetLang = findLang(cfg.target);
  const BackIcon = isRtl ? ArrowRight : ArrowLeft;
  const ForwardIcon = isRtl ? ArrowLeft : ArrowRight;

  const needsKey = cfg.providerId !== "ollama";
  const canStart =
    !!cfg.target &&
    !!cfg.shape &&
    !!cfg.model.trim() &&
    (!needsKey || !!cfg.apiKey.trim());

  const langOptions: ComboboxOption[] = LANGUAGES.map((l) => ({
    value: l.code,
    label: `${l.name} — ${l.english}${l.rtl ? " · RTL" : ""}`,
    sub: l.code,
  }));

  const providerOptions: ComboboxOption[] = PROVIDERS.map((p) => ({
    value: p.id,
    label: p.name,
    sub: p.defaultBase || "مخصّص",
  }));

  const modelOptions: ComboboxOption[] = provider.models.map((m) => ({
    value: m.id,
    label: m.id,
    sub: m.note,
  }));

  return (
    <div className="grid gap-8 animate-in fade-in slide-in-from-bottom-1 duration-300">
      <header className="flex items-center justify-between gap-4">
        <div className="grid gap-3">
          <div className="text-eyebrow">إعداد الترجمة · الخطوة ٢ من ٣</div>
          <h1 className="text-[28px] font-bold leading-tight tracking-[-0.005em] m-0">
            قرّر كيف ستبدو النتيجة.
          </h1>
        </div>
        <Button variant="ghost" size="sm" onClick={onBack}>
          <BackIcon className="h-4 w-4" />
          رجوع
        </Button>
      </header>

      <Card>
        <CardHead>
          <CardTitle>المستندات · {stagedFiles.length}</CardTitle>
          <span className="text-micro">
            المجموع {fmtSize(stagedFiles.reduce((a, f) => a + f.sizeKb, 0))}
          </span>
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
                <div className="text-[12px] text-paper-3 font-mono">
                  {fmtSize(f.sizeKb)}
                </div>
              </div>
              <Badge>جاهز</Badge>
            </div>
          ))}
        </div>
      </Card>

      <div className="grid lg:grid-cols-[1fr_2fr] gap-4">
        <Card>
          <CardHead>
            <CardTitle>اللغة الهدف</CardTitle>
          </CardHead>
          <div className="grid gap-2">
            <Label htmlFor="target-lang">تُرجِم إلى</Label>
            <Combobox
              id="target-lang"
              ariaLabel="لغة الهدف"
              value={cfg.target}
              onChange={(v) => setCfg({ ...cfg, target: v })}
              options={langOptions}
              placeholder="اختر لغة"
            />
          </div>
          <div className="mt-4 p-3 rounded-md border border-line bg-ink-1/40 flex gap-3 items-start text-[13px]">
            <Sparkles className="h-4 w-4 text-paper-2 mt-0.5 shrink-0" />
            <div>
              <div className="font-semibold text-paper-0">
                يُكشف مصدر اللغة تلقائيًا
              </div>
              <div className="text-paper-2 text-[12px] mt-0.5">
                لا حاجة لاختيار لغة المصدر — سيكتشفها النظام من محتوى الملف.
              </div>
            </div>
          </div>
          {targetLang.rtl && (
            <div className="mt-3 p-3 rounded-md border border-accent-line bg-accent-soft flex gap-3 items-start text-[13px]">
              <FlipHorizontal2 className="h-4 w-4 text-accent mt-0.5 shrink-0" />
              <div>
                <div className="font-semibold text-paper-0">
                  سيُعكَس التخطيط بالكامل
                </div>
                <div className="text-paper-2 text-[12px] mt-0.5">
                  تبديل مواضع كتل النص والصور مع الحفاظ على محتوى الصور كما هو.
                </div>
              </div>
            </div>
          )}
        </Card>

        <Card>
          <CardHead>
            <CardTitle>شكل الإخراج</CardTitle>
          </CardHead>
          <div
            className="grid grid-cols-2 xl:grid-cols-4 gap-3"
            role="radiogroup"
            aria-label="شكل الإخراج"
          >
            {OUTPUT_SHAPES.map((s) => {
              const selected = cfg.shape === s.id;
              return (
                <button
                  key={s.id}
                  type="button"
                  role="radio"
                  aria-checked={selected}
                  onClick={() => setCfg({ ...cfg, shape: s.id })}
                  className={cn(
                    "grid gap-3 p-4 rounded-md border bg-ink-1 text-start cursor-pointer transition-all",
                    "hover:bg-ink-2 hover:border-line-strong",
                    "focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-accent-line",
                    selected && "border-accent bg-gradient-to-b from-[oklch(0.74_0.15_155/0.08)] to-ink-1 shadow-[inset_0_0_0_1px_var(--color-accent-line)]",
                    !selected && "border-line"
                  )}
                >
                  <div className="aspect-video rounded-md border border-line bg-ink-0 p-2.5 grid place-items-center overflow-hidden">
                    <ShapeDiagram id={s.id} />
                  </div>
                  <div className="flex items-baseline justify-between gap-1.5">
                    <span className="font-semibold text-[14px]">{s.label}</span>
                    {selected && (
                      <span className="text-[11px] text-accent font-mono">محدَّد</span>
                    )}
                  </div>
                  <span className="text-[11px] text-paper-3 font-mono">{s.sub}</span>
                </button>
              );
            })}
          </div>
        </Card>
      </div>

      <Card>
        <CardHead>
          <CardTitle>بيانات اعتماد الذكاء الاصطناعي</CardTitle>
          <span className="text-micro" style={{ color: "var(--color-paper-3)" }}>
            تُستخدم فقط خلال هذه الطلبية · لا تُخزَّن على الخادم
          </span>
        </CardHead>

        <div className="grid md:grid-cols-2 gap-4">
          <div className="grid gap-2">
            <Label>المزوِّد</Label>
            <Combobox
              ariaLabel="المزوِّد"
              value={cfg.providerId}
              onChange={(v) => {
                const p = findProvider(v);
                setCfg({
                  ...cfg,
                  providerId: p.id,
                  apiBase: p.defaultBase,
                  model: p.models[0]?.id || cfg.model,
                });
              }}
              options={providerOptions}
            />
          </div>

          <div className="grid gap-2">
            <Label>اسم النموذج</Label>
            <Combobox
              ariaLabel="النموذج"
              value={cfg.model}
              onChange={(v) => setCfg({ ...cfg, model: v })}
              options={modelOptions}
              placeholder="ابحث أو اكتب اسمًا مخصّصًا"
              allowCustom
              monoValue
            />
          </div>

          <div className="grid gap-2">
            <Label>
              <span className="inline-flex items-center gap-1.5">
                <KeyRound className="h-3 w-3" />
                مفتاح الـ API
              </span>
            </Label>
            <SecretField
              value={cfg.apiKey}
              onChange={(v) => setCfg({ ...cfg, apiKey: v })}
              placeholder={provider.keyHint || "sk-…"}
            />
          </div>

          <div className="grid gap-2">
            <Label>عنوان الـ API (اختياري)</Label>
            <Input
              dir="ltr"
              className="font-mono text-[13px]"
              value={cfg.apiBase}
              onChange={(e) => setCfg({ ...cfg, apiBase: e.target.value })}
              placeholder={provider.defaultBase || "https://…"}
            />
          </div>
        </div>

        <details className="mt-4">
          <summary className="cursor-pointer text-paper-2 text-[13px] font-mono tracking-[0.06em] uppercase">
            خيارات متقدمة
          </summary>
          <div className="grid md:grid-cols-2 gap-4 mt-3.5">
            <div className="grid gap-2">
              <Label>درجة العشوائية</Label>
              <Input
                dir="ltr"
                type="number"
                step="0.1"
                min="0"
                max="2"
                value={cfg.temperature}
                onChange={(e) => setCfg({ ...cfg, temperature: e.target.value })}
              />
            </div>
            <div className="grid gap-2">
              <Label>حجم القطعة (رموز)</Label>
              <Input
                dir="ltr"
                type="number"
                min="200"
                max="8000"
                step="100"
                value={cfg.chunkSize}
                onChange={(e) => setCfg({ ...cfg, chunkSize: e.target.value })}
              />
            </div>
            <div className="grid gap-2 md:col-span-2">
              <Label>توجيهات إضافية للمترجم</Label>
              <Textarea
                value={cfg.glossary}
                onChange={(e) => setCfg({ ...cfg, glossary: e.target.value })}
                placeholder="مثال: احتفظ بأسماء العلم كما هي. استخدم اللغة العربية الفصحى الحديثة."
              />
            </div>
          </div>
        </details>
      </Card>

      <div className="flex items-center justify-between">
        <div className="text-micro">الخطوة ٢ من ٣</div>
        <div className="flex items-center gap-3">
          <Button variant="ghost" onClick={onBack}>رجوع</Button>
          <Button
            variant="accent"
            disabled={!canStart || starting}
            onClick={onStart}
            aria-busy={starting}
          >
            {starting ? (
              <>
                <Loader2 className="h-4 w-4 animate-spin" />
                جاري البدء…
              </>
            ) : (
              <>
                <Sparkles className="h-4 w-4" />
                بدء الترجمة
                <ForwardIcon className="h-4 w-4" />
              </>
            )}
          </Button>
        </div>
      </div>
    </div>
  );
}
