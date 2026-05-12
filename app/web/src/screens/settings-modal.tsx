import * as React from "react";
import { AlertTriangle, Check, KeyRound, ServerCog, X } from "lucide-react";
import {
  Dialog,
  DialogBody,
  DialogContent,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import { Button } from "@/components/ui/button";
import { Input, Textarea } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Combobox, type ComboboxOption } from "@/components/ui/combobox";
import { SecretField } from "@/components/ui/secret-field";
import {
  type Config,
  getSharedKey,
  isConfigured,
  useStoreSubscription,
} from "@/lib/store";
import { PROVIDERS, findProvider } from "@/lib/data";
import { cn } from "@/lib/utils";

interface Props {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  cfg: Config;
  setCfg: (c: Config) => void;
  /** If true, the dialog can only be closed once the config is valid. */
  enforceGate?: boolean;
}

export function SettingsModal({ open, onOpenChange, cfg, setCfg, enforceGate }: Props) {
  useStoreSubscription();
  const shared = getSharedKey();
  const sharedProvider = shared ? findProvider(shared.provider) : null;

  // Two views inside the modal:
  //   "gate" — just the two big choice buttons (only when shared is available)
  //   "form" — the full provider/model/key/api_base/glossary form + save
  // Default to gate whenever shared is available so the user can switch back
  // and forth across visits without digging.
  const [view, setView] = React.useState<"gate" | "form">(
    shared ? "gate" : "form"
  );
  const [local, setLocal] = React.useState<Config>(cfg);

  React.useEffect(() => {
    if (open) {
      setLocal(cfg);
      setView(shared ? "gate" : "form");
    }
  }, [open, cfg, shared]);

  const provider = findProvider(local.providerId);
  const valid = isConfigured(local);

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

  function pickShared() {
    if (!shared || !sharedProvider) return;
    // Apply the shared config to the parent and close immediately — no Save
    // step required, that's the whole point of the shortcut.
    setCfg({
      ...cfg,
      keyMode: "shared",
      providerId: shared.provider,
      model: sharedProvider.models[0]?.id || cfg.model,
      apiBase: sharedProvider.defaultBase,
      apiKey: "",
    });
    onOpenChange(false);
  }

  function pickPersonal() {
    setLocal({ ...local, keyMode: "own", apiKey: "" });
    setView("form");
  }

  function save() {
    setCfg({ ...local, keyMode: "own" });
    onOpenChange(false);
  }

  function tryClose(o: boolean) {
    if (!o && enforceGate && !isConfigured(cfg)) return;
    onOpenChange(o);
  }

  const sharedCaveat = sharedProvider?.sharedCaveat;

  return (
    <Dialog open={open} onOpenChange={tryClose}>
      <DialogContent hideClose>
        <DialogHeader>
          <div>
            <div className="text-micro">الإعدادات</div>
            <DialogTitle className="mt-1">
              {view === "gate" ? "اختر طريقة الترجمة" : "الافتراضيات + بيانات الاعتماد"}
            </DialogTitle>
          </div>
          {!enforceGate && (
            <Button
              variant="ghost"
              size="iconSm"
              onClick={() => onOpenChange(false)}
              aria-label="إغلاق"
            >
              <X className="h-4 w-4" />
            </Button>
          )}
        </DialogHeader>

        {view === "gate" && shared && sharedProvider ? (
          <DialogBody>
            <div className="grid md:grid-cols-2 gap-3">
              <button
                type="button"
                onClick={pickShared}
                className={cn(
                  "grid gap-3 p-5 rounded-md border text-start cursor-pointer transition-all min-h-[180px]",
                  "border-accent bg-gradient-to-b from-[oklch(0.74_0.15_155/0.10)] to-ink-1",
                  "shadow-[inset_0_0_0_1px_var(--color-accent-line)]",
                  "hover:brightness-110 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-accent-line"
                )}
              >
                <div className="inline-flex items-center gap-2">
                  <ServerCog className="h-5 w-5 text-accent" />
                  <span className="font-bold text-[16px]">
                    استخدم مفتاح الخادم المشترك
                  </span>
                </div>
                <div className="text-[13px] text-paper-1">
                  المزوِّد:{" "}
                  <span className="font-mono text-paper-0">{sharedProvider.name}</span>
                </div>
                {sharedCaveat && (
                  <div className="p-2.5 rounded-md border border-[oklch(0.82_0.13_82/0.3)] bg-[oklch(0.82_0.13_82/0.08)] flex gap-2 items-start text-[12px]">
                    <AlertTriangle className="h-3.5 w-3.5 text-warn shrink-0 mt-0.5" />
                    <div className="text-paper-1">{sharedCaveat}</div>
                  </div>
                )}
              </button>

              <button
                type="button"
                onClick={pickPersonal}
                className={cn(
                  "grid gap-3 p-5 rounded-md border border-line bg-ink-1 text-start cursor-pointer transition-all min-h-[180px]",
                  "hover:bg-ink-2 hover:border-line-strong focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-accent-line"
                )}
              >
                <div className="inline-flex items-center gap-2">
                  <KeyRound className="h-5 w-5 text-paper-2" />
                  <span className="font-bold text-[16px]">
                    استخدم مفتاحك الخاص
                  </span>
                </div>
                <div className="text-[13px] text-paper-2">
                  أدخل مزوِّدًا ونموذجًا ومفتاح API خاصًا بك. مفيد إذا أردت
                  التحكّم الكامل أو استخدام مزوِّد آخر.
                </div>
              </button>
            </div>
          </DialogBody>
        ) : (
          <>
            <DialogBody>
              {enforceGate && (
                <div className="p-3 rounded-md border border-[oklch(0.82_0.13_82/0.3)] bg-[oklch(0.82_0.13_82/0.08)] flex gap-3 items-start text-[13px]">
                  <AlertTriangle className="h-4 w-4 text-warn shrink-0 mt-0.5" />
                  <div className="text-paper-1">
                    هذه أول مرة تستخدم فيها س‑ترجم على هذا المتصفح. حدّد المزوِّد
                    ومفتاح الـ API والنموذج قبل البدء. يُخزَّن كل شيء محليًا فقط.
                  </div>
                </div>
              )}

              <div className="grid md:grid-cols-2 gap-3.5">
                <div className="grid gap-2">
                  <Label>المزوِّد</Label>
                  <Combobox
                    ariaLabel="المزوِّد"
                    value={local.providerId}
                    onChange={(v) => {
                      const p = findProvider(v);
                      setLocal({
                        ...local,
                        providerId: p.id,
                        apiBase: p.defaultBase,
                        model: p.models[0]?.id || local.model,
                      });
                    }}
                    options={providerOptions}
                  />
                </div>
                <div className="grid gap-2">
                  <Label>النموذج</Label>
                  <Combobox
                    ariaLabel="النموذج"
                    value={local.model}
                    onChange={(v) => setLocal({ ...local, model: v })}
                    options={modelOptions}
                    placeholder="ابحث أو اكتب اسمًا مخصّصًا"
                    allowCustom
                    monoValue
                  />
                </div>
                <div className="grid gap-2">
                  <Label>مفتاح الـ API</Label>
                  <SecretField
                    value={local.apiKey}
                    onChange={(v) => setLocal({ ...local, apiKey: v })}
                    placeholder={provider.keyHint || "sk-…"}
                  />
                </div>
                <div className="grid gap-2">
                  <Label>عنوان الـ API</Label>
                  <Input
                    dir="ltr"
                    className="font-mono text-[13px]"
                    value={local.apiBase}
                    onChange={(e) => setLocal({ ...local, apiBase: e.target.value })}
                    placeholder={provider.defaultBase || "https://…"}
                  />
                </div>
              </div>

              <div className="h-px bg-line my-1" />

              <div className="grid gap-2">
                <Label>تعليمات دائمة للمترجم</Label>
                <Textarea
                  value={local.glossary}
                  onChange={(e) => setLocal({ ...local, glossary: e.target.value })}
                  placeholder="احتفظ بأسماء العلم. استخدم الفصحى الحديثة."
                />
              </div>

              <div className="p-3 rounded-md border border-[oklch(0.82_0.13_82/0.3)] bg-[oklch(0.82_0.13_82/0.08)] flex gap-2.5 items-start text-[12px]">
                <AlertTriangle className="h-4 w-4 text-warn shrink-0 mt-0.5" />
                <div className="text-paper-1">
                  يُخزَّن مفتاح الـ API في هذا المتصفح فقط (localStorage). لا
                  يُرسَل إلى الخادم إلا خلال عملية ترجمة نشطة.
                </div>
              </div>
            </DialogBody>

            <DialogFooter>
              <div className="flex items-center gap-3">
                {shared && (
                  <Button variant="ghost" size="sm" onClick={() => setView("gate")}>
                    رجوع للاختيار
                  </Button>
                )}
                <span className="text-micro">
                  {valid ? "جاهز للبدء" : "أكمل الحقول المطلوبة"}
                </span>
              </div>
              <div className="flex gap-2 items-center">
                {!enforceGate && (
                  <Button variant="ghost" onClick={() => onOpenChange(false)}>
                    إلغاء
                  </Button>
                )}
                <Button variant="accent" disabled={!valid} onClick={save}>
                  <Check className="h-4 w-4" />
                  حفظ
                </Button>
              </div>
            </DialogFooter>
          </>
        )}
      </DialogContent>
    </Dialog>
  );
}
