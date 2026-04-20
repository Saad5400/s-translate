// Reference data: languages, providers + current (2026) model lists,
// output shapes, supported file types. Custom model names are allowed
// everywhere via the combobox `allowCustom` prop.

export type Language = {
  code: string;
  name: string; // Arabic display name
  english: string;
  rtl: boolean;
};

export const LANGUAGES: Language[] = [
  { code: "ar", name: "العربية", english: "Arabic", rtl: true },
  { code: "en", name: "الإنجليزية", english: "English", rtl: false },
  { code: "fr", name: "الفرنسية", english: "French", rtl: false },
  { code: "es", name: "الإسبانية", english: "Spanish", rtl: false },
  { code: "de", name: "الألمانية", english: "German", rtl: false },
  { code: "zh", name: "الصينية", english: "Chinese (Simplified)", rtl: false },
  { code: "ja", name: "اليابانية", english: "Japanese", rtl: false },
  { code: "ko", name: "الكورية", english: "Korean", rtl: false },
  { code: "ru", name: "الروسية", english: "Russian", rtl: false },
  { code: "tr", name: "التركية", english: "Turkish", rtl: false },
  { code: "fa", name: "الفارسية", english: "Persian", rtl: true },
  { code: "ur", name: "الأردية", english: "Urdu", rtl: true },
  { code: "he", name: "العبرية", english: "Hebrew", rtl: true },
  { code: "hi", name: "الهندية", english: "Hindi", rtl: false },
  { code: "bn", name: "البنغالية", english: "Bengali", rtl: false },
  { code: "pt", name: "البرتغالية", english: "Portuguese", rtl: false },
  { code: "it", name: "الإيطالية", english: "Italian", rtl: false },
  { code: "nl", name: "الهولندية", english: "Dutch", rtl: false },
  { code: "pl", name: "البولندية", english: "Polish", rtl: false },
  { code: "sv", name: "السويدية", english: "Swedish", rtl: false },
  { code: "id", name: "الإندونيسية", english: "Indonesian", rtl: false },
  { code: "vi", name: "الفيتنامية", english: "Vietnamese", rtl: false },
  { code: "th", name: "التايلاندية", english: "Thai", rtl: false },
  { code: "uk", name: "الأوكرانية", english: "Ukrainian", rtl: false },
  { code: "ms", name: "الماليزية", english: "Malay", rtl: false },
  { code: "ta", name: "التاميلية", english: "Tamil", rtl: false },
  { code: "el", name: "اليونانية", english: "Greek", rtl: false },
  { code: "cs", name: "التشيكية", english: "Czech", rtl: false },
  { code: "ro", name: "الرومانية", english: "Romanian", rtl: false },
  { code: "hu", name: "الهنغارية", english: "Hungarian", rtl: false },
  { code: "fi", name: "الفنلندية", english: "Finnish", rtl: false },
  { code: "da", name: "الدانماركية", english: "Danish", rtl: false },
  { code: "no", name: "النرويجية", english: "Norwegian", rtl: false },
  { code: "sw", name: "السواحيلية", english: "Swahili", rtl: false },
];

export function findLang(code: string): Language {
  return LANGUAGES.find((l) => l.code === code) ?? LANGUAGES[0];
}

export type ProviderId =
  | "openai"
  | "anthropic"
  | "deepseek"
  | "gemini"
  | "groq"
  | "mistral"
  | "ollama"
  | "openrouter"
  | "custom";

export type Provider = {
  id: ProviderId;
  name: string;
  defaultBase: string;
  models: { id: string; note?: string }[];
  keyHint?: string;
};

// Real current model lists (2026). Custom model names are always allowed.
export const PROVIDERS: Provider[] = [
  {
    id: "anthropic",
    name: "Anthropic",
    defaultBase: "https://api.anthropic.com/v1",
    keyHint: "sk-ant-…",
    models: [
      { id: "claude-opus-4-7", note: "الأكثر قدرة" },
      { id: "claude-sonnet-4-6", note: "متوازن · الافتراضي" },
      { id: "claude-haiku-4-5", note: "سريع" },
      { id: "claude-opus-4-1" },
      { id: "claude-sonnet-4-5" },
    ],
  },
  {
    id: "openai",
    name: "OpenAI",
    defaultBase: "https://api.openai.com/v1",
    keyHint: "sk-…",
    models: [
      { id: "gpt-5", note: "الأعلى" },
      { id: "gpt-5-mini" },
      { id: "gpt-4.1" },
      { id: "gpt-4o" },
      { id: "gpt-4o-mini", note: "اقتصادي" },
      { id: "o3" },
      { id: "o3-mini", note: "استدلالي" },
      { id: "o1" },
    ],
  },
  {
    id: "deepseek",
    name: "DeepSeek",
    defaultBase: "https://api.deepseek.com/v1",
    keyHint: "sk-…",
    models: [
      { id: "deepseek-chat", note: "جيد في العربية · رخيص" },
      { id: "deepseek-reasoner" },
    ],
  },
  {
    id: "gemini",
    name: "Google Gemini",
    defaultBase: "https://generativelanguage.googleapis.com/v1beta",
    keyHint: "AIza…",
    models: [
      { id: "gemini-2.5-pro" },
      { id: "gemini-2.5-flash", note: "سريع" },
      { id: "gemini-2.5-flash-lite" },
      { id: "gemini-1.5-pro" },
      { id: "gemini-1.5-flash" },
    ],
  },
  {
    id: "groq",
    name: "Groq",
    defaultBase: "https://api.groq.com/openai/v1",
    keyHint: "gsk_…",
    models: [
      { id: "llama-3.3-70b-versatile", note: "فائق السرعة" },
      { id: "llama-3.1-70b-versatile" },
      { id: "llama-3.1-8b-instant" },
      { id: "mixtral-8x7b-32768" },
    ],
  },
  {
    id: "mistral",
    name: "Mistral",
    defaultBase: "https://api.mistral.ai/v1",
    models: [
      { id: "mistral-large-latest" },
      { id: "mistral-medium-latest" },
      { id: "mistral-small-latest" },
      { id: "codestral-latest" },
    ],
  },
  {
    id: "ollama",
    name: "Ollama (محلي)",
    defaultBase: "http://localhost:11434",
    keyHint: "غير مطلوب",
    models: [
      { id: "llama3.3" },
      { id: "qwen2.5:72b" },
      { id: "aya:35b", note: "متعدد اللغات" },
      { id: "mistral" },
      { id: "gemma2:27b" },
    ],
  },
  {
    id: "openrouter",
    name: "OpenRouter",
    defaultBase: "https://openrouter.ai/api/v1",
    keyHint: "sk-or-…",
    models: [
      { id: "anthropic/claude-sonnet-4.6" },
      { id: "openai/gpt-4.1" },
      { id: "meta-llama/llama-3.3-70b-instruct" },
      { id: "qwen/qwen-2.5-72b-instruct" },
    ],
  },
  {
    id: "custom",
    name: "مخصّص (متوافق مع OpenAI)",
    defaultBase: "",
    models: [],
  },
];

export function findProvider(id: string): Provider {
  return PROVIDERS.find((p) => p.id === id) ?? PROVIDERS[0];
}

export type OutputShape = {
  id: "translated" | "original" | "stacked" | "side-by-side";
  label: string;
  sub: string;
  // maps to backend OutputMode
  backend: "translated" | "original" | "both_vertical" | "both_horizontal";
};

export const OUTPUT_SHAPES: OutputShape[] = [
  { id: "translated", label: "المُترجَم فقط", sub: "ملف واحد باللغة الهدف", backend: "translated" },
  { id: "original", label: "الأصل فقط", sub: "تمرير بدون تعديل", backend: "original" },
  { id: "stacked", label: "مكدّس رأسيًا", sub: "الأصل ثم الترجمة في ملف واحد", backend: "both_vertical" },
  { id: "side-by-side", label: "جنبًا إلى جنب", sub: "الأصل والترجمة على نفس الصفحة", backend: "both_horizontal" },
];

export function findShape(id: string): OutputShape {
  return OUTPUT_SHAPES.find((s) => s.id === id) ?? OUTPUT_SHAPES[0];
}

export const FILE_TYPES = [
  { ext: "DOCX", label: "Word" },
  { ext: "PPTX", label: "PowerPoint" },
  { ext: "XLSX", label: "Excel" },
  { ext: "PDF", label: "PDF" },
  { ext: "TXT", label: "نص عادي" },
];

export const ACCEPT_EXTENSIONS = ".docx,.pptx,.xlsx,.pdf,.txt";

export const STEPS = [
  { id: "extract", label: "استخراج النص" },
  { id: "chunk", label: "تقطيع + إعداد" },
  { id: "translate", label: "الترجمة" },
  { id: "rtl", label: "تطبيق RTL + المرآة" },
  { id: "assemble", label: "إعادة التجميع" },
];
