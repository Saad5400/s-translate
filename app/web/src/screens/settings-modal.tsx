import * as React from "react";
import { AlertTriangle, Check, X } from "lucide-react";
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
import { type Config, isConfigured } from "@/lib/store";
import { PROVIDERS, findProvider } from "@/lib/data";

interface Props {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  cfg: Config;
  setCfg: (c: Config) => void;
  /** If true, the dialog can only be closed once the config is valid. */
  enforceGate?: boolean;
}

export function SettingsModal({ open, onOpenChange, cfg, setCfg, enforceGate }: Props) {
  const [local, setLocal] = React.useState<Config>(cfg);

  React.useEffect(() => {
    if (open) setLocal(cfg);
  }, [open, cfg]);

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

  function save() {
    setCfg(local);
    onOpenChange(false);
  }

  function tryClose(open: boolean) {
    if (!open && enforceGate && !isConfigured(cfg)) return; // block close
    onOpenChange(open);
  }

  return (
    <Dialog open={open} onOpenChange={tryClose}>
      <DialogContent hideClose>
        <DialogHeader>
          <div>
            <div className="text-micro">الإعدادات</div>
            <DialogTitle className="mt-1">
              الافتراضيات + بيانات الاعتماد
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

        <DialogBody>
          {enforceGate && (
            <div className="p-3 rounded-md border border-[oklch(0.82_0.13_82/0.3)] bg-[oklch(0.82_0.13_82/0.08)] flex gap-3 items-start text-[13px]">
              <AlertTriangle className="h-4 w-4 text-warn shrink-0 mt-0.5" />
              <div className="text-paper-1">
                هذه أول مرة تستخدم فيها س‑ترجم على هذا المتصفح. حدّد المزوِّد ومفتاح
                الـ API والنموذج قبل البدء. يُخزَّن كل شيء محليًا فقط.
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
              يُخزَّن مفتاح الـ API في هذا المتصفح فقط (localStorage). لا يُرسَل إلى
              الخادم إلا خلال عملية ترجمة نشطة.
            </div>
          </div>
        </DialogBody>

        <DialogFooter>
          <span className="text-micro">
            {valid ? "جاهز للبدء" : "أكمل الحقول المطلوبة"}
          </span>
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
      </DialogContent>
    </Dialog>
  );
}
