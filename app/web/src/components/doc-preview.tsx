// Generic translated-document illustration used on the progress and result
// screens. We don't have access to the real document content on the
// client — this is a representative preview that animates the translation
// pipeline visually.

import { cn } from "@/lib/utils";

const SAMPLE = [
  {
    en: "In the fourth quarter, the Company delivered record-setting revenue driven by accelerated adoption across its enterprise customer base.",
    ar: "حققت الشركة في الربع الرابع إيرادات قياسية مدفوعة بتسارع التبنّي لدى قاعدة عملائها من المؤسسات.",
  },
  {
    en: "Gross margin expanded by two hundred and thirty basis points year-over-year, reflecting disciplined cost management.",
    ar: "اتسع هامش الربح الإجمالي بمقدار مئتين وثلاثين نقطة أساس على أساس سنوي، مما يعكس إدارةً منضبطة للتكاليف.",
  },
  {
    en: "Operating cash flow totaled four hundred and twelve million dollars, the highest in the Company's history.",
    ar: "بلغ التدفق النقدي التشغيلي أربعمئة واثني عشر مليون دولار، وهو الأعلى في تاريخ الشركة.",
  },
  {
    en: "Regionally, Europe and the Middle East led growth with a thirty-one percent increase.",
    ar: "قادت أوروبا والشرق الأوسط النمو بزيادة قدرها واحد وثلاثون بالمئة.",
  },
  {
    en: "Looking ahead, management expects full-year guidance to trend toward the upper end of the range.",
    ar: "تتوقع الإدارة أن تميل التوجيهات السنوية إلى الطرف الأعلى من النطاق المُعلن.",
  },
];

export function DocPage({
  mode,
  revealCount = 99,
  showScan = false,
}: {
  mode: "original" | "translated";
  revealCount?: number;
  showScan?: boolean;
}) {
  if (mode === "original") {
    return (
      <div className="doc-page" dir="ltr">
        <h4>Q4 Earnings Brief</h4>
        <p className="dim">Q4 FY25 · Investor Relations · Confidential</p>
        {SAMPLE.map((p, i) => (
          <p key={i}>{p.en}</p>
        ))}
      </div>
    );
  }
  return (
    <div className="doc-page rtl" dir="rtl">
      <h4>ملخص أرباح الربع الرابع</h4>
      <p className="dim">الربع الرابع من السنة المالية ٢٠٢٥ · علاقات المستثمرين · سرّي</p>
      {SAMPLE.map((p, i) => (
        <p key={i} className={i >= revealCount ? "opacity-40" : ""}>
          {i < revealCount ? (
            <span className={i === revealCount - 1 ? "chunk-pulse" : ""}>
              {p.ar}
            </span>
          ) : (
            <span>{p.en.replace(/[a-zA-Z0-9]/g, "•")}</span>
          )}
        </p>
      ))}
      {showScan && revealCount < SAMPLE.length && <div className="scan" />}
    </div>
  );
}

export function DocPageForFile({
  fileName,
  pages,
  mode,
  className,
}: {
  fileName: string;
  pages?: number;
  mode: "original" | "translated";
  className?: string;
}) {
  const title = fileName.replace(/\.[^.]+$/, "");
  return (
    <div className={cn("doc-page", mode === "translated" && "rtl", className)} dir={mode === "translated" ? "rtl" : "ltr"}>
      <h4>{mode === "translated" ? "النسخة المُترجمة" : title}</h4>
      <p className="dim">
        {mode === "translated"
          ? `تم الحفاظ على الخطوط والأنماط — ${pages ?? ""} صفحة`
          : `${title} · ${pages ?? ""} pages`}
      </p>
      {[0, 1, 2, 3, 4].map((i) => (
        <p key={i} className={i > 2 ? "dim" : ""}>
          {mode === "translated"
            ? "هذا عرض تخطيطي للنتيجة المتوقعة — ستحتفظ الوثيقة الأصلية بمحتواها الكامل مع ترجمة دقيقة للنصوص."
            : "This is a layout illustration of the source document — the actual file content is preserved exactly as uploaded."}
        </p>
      ))}
    </div>
  );
}
