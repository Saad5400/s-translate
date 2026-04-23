// Live document preview shown on the progress and result screens. When the
// parent passes `original` / `translated` paragraph arrays (fetched from
// /api/jobs/{id}/preview), we render real text from the user's uploaded and
// translated artifacts. Until then we show a scrubbed placeholder so the
// layout doesn't collapse.

import { cn } from "@/lib/utils";

const PLACEHOLDER_LINES = [
  "………………………………………………………………",
  "…………………………………………………………………………",
  "………………………………………………………",
  "………………………………………………………………",
  "…………………………………………………………………………",
];

export function DocPage({
  mode,
  revealCount = 99,
  showScan = false,
  title,
  subtitle,
  original,
  translated,
}: {
  mode: "original" | "translated";
  revealCount?: number;
  showScan?: boolean;
  title?: string;
  subtitle?: string;
  original?: string[];
  translated?: string[];
}) {
  if (mode === "original") {
    const paras = original && original.length ? original : PLACEHOLDER_LINES;
    return (
      <div className="doc-page" dir="auto">
        {title && <h4><bdi>{title}</bdi></h4>}
        {subtitle && <p className="dim">{subtitle}</p>}
        {paras.map((p, i) => (
          <p key={i} dir="auto">{p}</p>
        ))}
      </div>
    );
  }

  const origParas = original && original.length ? original : PLACEHOLDER_LINES;
  const hasTranslated = translated && translated.length > 0;
  const arParas = hasTranslated ? translated! : origParas;
  const total = arParas.length;
  const reveal = hasTranslated ? total : Math.min(total, Math.max(0, revealCount));

  return (
    <div className="doc-page rtl" dir="rtl">
      {title && <h4><bdi>{title}</bdi></h4>}
      {subtitle && <p className="dim">{subtitle}</p>}
      {arParas.map((p, i) => {
        const revealed = i < reveal;
        return (
          <p key={i} className={revealed ? "" : "opacity-40"} dir="auto">
            {revealed ? (
              <span className={i === reveal - 1 && !hasTranslated ? "chunk-pulse" : ""}>
                {p}
              </span>
            ) : (
              <span>{(origParas[i] ?? p).replace(/[^\s·.,;:!?()\[\]"'—-]/g, "•")}</span>
            )}
          </p>
        );
      })}
      {showScan && reveal < total && <div className="scan" />}
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
