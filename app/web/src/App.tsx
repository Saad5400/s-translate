import * as React from "react";
import { Settings, ChevronLeft, ChevronRight } from "lucide-react";
import { Button } from "@/components/ui/button";
import { ToastProvider, useToast } from "@/components/ui/toast";
import { Sidebar } from "@/screens/sidebar";
import { UploadScreen, type StagedFile } from "@/screens/upload";
import { ConfigureScreen } from "@/screens/configure";
import { ProgressScreen, type LogEntry } from "@/screens/progress";
import { ResultScreen } from "@/screens/result";
import { SettingsModal } from "@/screens/settings-modal";
import {
  loadConfig,
  saveConfig,
  loadTweaks,
  saveTweaks,
  loadRoute,
  saveRoute,
  isConfigured,
  type Config,
} from "@/lib/store";
import {
  createJob,
  getJob,
  getJobPreview,
  listJobs,
  pollJob,
  type JobMeta,
  type JobPreview,
} from "@/lib/api";
import { findShape, LANGUAGES } from "@/lib/data";

type RouteName = "upload" | "configure" | "progress" | "result";
interface Route {
  name: RouteName;
  jobId?: string;
}

function AppInner() {
  const { toast } = useToast();

  const [cfg, setCfgState] = React.useState<Config>(loadConfig);
  const setCfg = React.useCallback((c: Config) => {
    setCfgState(c);
    saveConfig(c);
  }, []);

  const [tweaks, setTweaksState] = React.useState(loadTweaks);
  React.useEffect(() => {
    document.documentElement.setAttribute("data-accent", tweaks.accent);
    document.documentElement.setAttribute("data-density", tweaks.density);
  }, [tweaks]);

  const [route, setRouteState] = React.useState<Route>(() => {
    const r = loadRoute();
    return r.name === "progress" || r.name === "result"
      ? (r as Route)
      : { name: "upload" };
  });
  const setRoute = React.useCallback((r: Route) => {
    setRouteState(r);
    saveRoute(r);
  }, []);

  const [stagedFiles, setStagedFiles] = React.useState<StagedFile[]>([]);
  const [jobs, setJobs] = React.useState<JobMeta[]>([]);
  const [activeJob, setActiveJob] = React.useState<JobMeta | null>(null);
  const [activeFileName, setActiveFileName] = React.useState<string>("");
  const [activePreview, setActivePreview] = React.useState<JobPreview | null>(null);
  const [logs, setLogs] = React.useState<LogEntry[]>([]);
  const [settingsOpen, setSettingsOpen] = React.useState(false);
  const [starting, setStarting] = React.useState(false);
  const startingRef = React.useRef(false);

  // Gate on first-run configuration
  const configured = isConfigured(cfg);
  React.useEffect(() => {
    if (!configured) setSettingsOpen(true);
  }, [configured]);

  // Initial data load
  React.useEffect(() => {
    listJobs(50)
      .then((list) => setJobs(list))
      .catch(() => {});
    // Check URL hash for #job=XXX
    const hash = new URLSearchParams(window.location.hash.slice(1));
    const jobId = hash.get("job");
    if (jobId) resumeJob(jobId);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  // Rehydrate active job meta if route points at one
  React.useEffect(() => {
    if ((route.name === "progress" || route.name === "result") && route.jobId) {
      getJob(route.jobId)
        .then((m) => {
          if (!m) {
            setRoute({ name: "upload" });
            return;
          }
          setActiveJob(m);
          setActiveFileName(m.input_name);
          if (m.status === "running" || m.status === "queued") {
            startPolling(m.id);
            setRoute({ name: "progress", jobId: m.id });
          } else {
            setRoute({ name: "result", jobId: m.id });
          }
        })
        .catch(() => setRoute({ name: "upload" }));
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  // Fetch real paragraph previews (original + translated) for the active job
  // so the progress/result screens render the user's actual document text
  // instead of placeholders. Re-fetch on status change — the translated
  // artifact only lands on disk once the job reaches "done", and we want the
  // preview to upgrade from original-only to side-by-side real text then.
  React.useEffect(() => {
    if (!activeJob) {
      setActivePreview(null);
      return;
    }
    let cancelled = false;
    getJobPreview(activeJob.id)
      .then((p) => {
        if (!cancelled) setActivePreview(p);
      })
      .catch(() => {});
    return () => {
      cancelled = true;
    };
  }, [activeJob?.id, activeJob?.status]);

  // localStorage sync for language (body lang) when target changes
  React.useEffect(() => {
    const l = LANGUAGES.find((x) => x.code === cfg.target);
    // Keep UI RTL (Arabic) regardless of target; only set lang attr for a11y
    document.documentElement.setAttribute("lang", l?.code ?? "ar");
  }, [cfg.target]);

  const isRtl = document.documentElement.dir === "rtl";

  // ─────────── actions ───────────

  function handleAddFile(f: StagedFile) {
    setStagedFiles((prev) => [...prev, f]);
  }
  function handleRemoveFile(idx: number) {
    setStagedFiles((prev) => prev.filter((_, i) => i !== idx));
  }

  async function handleStartJob() {
    // Guard against double-clicks / rapid re-submits producing duplicate jobs.
    if (startingRef.current) return;
    const first = stagedFiles[0];
    if (!first) return;
    if (!isConfigured(cfg)) {
      setSettingsOpen(true);
      return;
    }
    startingRef.current = true;
    setStarting(true);
    setLogs([]);
    const shape = findShape(cfg.shape);
    try {
      const { id, deduped, status } = await createJob({
        file: first.file,
        target_lang: cfg.target,
        provider: cfg.providerId === "custom" ? "openai" : cfg.providerId,
        model: cfg.model,
        api_key: cfg.apiKey,
        api_base: cfg.apiBase || undefined,
        temperature: parseFloat(cfg.temperature) || 0.2,
        output_mode: shape.backend,
        max_chunk_tokens: parseInt(cfg.chunkSize, 10) || 2500,
      });
      // Refresh job list so sidebar history reflects the (re)used job.
      const list = await listJobs(50).catch(() => []);
      setJobs(list);
      const fresh = (await getJob(id).catch(() => null)) ?? null;
      const meta: JobMeta =
        fresh ?? {
          id,
          status: (status ?? "queued") as JobMeta["status"],
          progress: 0,
          message: "queued",
          target_lang: cfg.target,
          provider: cfg.providerId,
          model: cfg.model,
          output_mode: shape.backend,
          input_name: first.name,
          output_name: "",
          created_at: Date.now() / 1000,
          updated_at: Date.now() / 1000,
          error: null,
        };
      setActiveJob(meta);
      setActiveFileName(meta.input_name || first.name);
      const url = new URL(window.location.href);
      url.hash = `job=${id}`;
      history.replaceState(null, "", url.toString());
      // Clear the staged upload — we're done with it either way.
      setStagedFiles([]);

      if (deduped) {
        toast({
          variant: "ok",
          title: "هذا الملف مُترجَم مسبقًا",
          description: "تم فتح الترجمة السابقة بدلًا من إعادة التنفيذ.",
        });
      }

      if (meta.status === "done" || meta.status === "failed") {
        setRoute({ name: "result", jobId: id });
      } else {
        setRoute({ name: "progress", jobId: id });
        startPolling(id);
      }
    } catch (e) {
      toast({
        variant: "error",
        title: "تعذّر إنشاء الطلبية",
        description: String(e instanceof Error ? e.message : e),
      });
    } finally {
      startingRef.current = false;
      setStarting(false);
    }
  }

  const pollAbortRef = React.useRef<AbortController | null>(null);
  function startPolling(id: string) {
    pollAbortRef.current?.abort();
    const ac = new AbortController();
    pollAbortRef.current = ac;
    const t0 = Date.now();
    pollJob(id, {
      intervalMs: 1000,
      signal: ac.signal,
      onUpdate: (m) => {
        setActiveJob(m);
        setJobs((prev) => {
          const i = prev.findIndex((x) => x.id === m.id);
          if (i === -1) return [m, ...prev];
          const copy = prev.slice();
          copy[i] = m;
          return copy;
        });
        setLogs((prev) => {
          const level = m.status === "failed" ? "err" : m.message.toLowerCase().includes("done") ? "ok" : "info";
          const t = (Date.now() - t0) / 1000;
          if (prev.length && prev[prev.length - 1].msg === m.message) return prev;
          return [...prev, { t, level, msg: m.message || m.status }];
        });
      },
    })
      .then((m) => {
        setActiveJob(m);
        if (m.status === "done") {
          setRoute({ name: "result", jobId: m.id });
          toast({ variant: "ok", title: "اكتملت الترجمة", description: m.input_name });
        } else if (m.status === "failed") {
          setRoute({ name: "result", jobId: m.id });
          toast({
            variant: "error",
            title: "فشلت الترجمة",
            description: m.error || "سبب غير معروف.",
          });
        }
      })
      .catch((err) => {
        if ((err as Error).name !== "AbortError") {
          toast({ variant: "error", title: "خطأ في المتابعة", description: String(err) });
        }
      });
  }

  function handleCancelJob() {
    pollAbortRef.current?.abort();
    setActiveJob(null);
    setStagedFiles([]);
    setRoute({ name: "upload" });
  }

  async function resumeJob(id: string) {
    const m = await getJob(id).catch(() => null);
    if (!m) {
      toast({
        variant: "warn",
        title: "لا يوجد عمل بالمُعرِّف",
        description: `قد يكون انتهت صلاحيته (٧ أيام) أو أنه على خادم آخر. (${id})`,
      });
      return;
    }
    setActiveJob(m);
    setActiveFileName(m.input_name);
    if (m.status === "running" || m.status === "queued") {
      setRoute({ name: "progress", jobId: id });
      startPolling(id);
    } else {
      setRoute({ name: "result", jobId: id });
    }
  }

  function handleNewJob() {
    pollAbortRef.current?.abort();
    setStagedFiles([]);
    setActiveJob(null);
    setRoute({ name: "upload" });
    const url = new URL(window.location.href);
    url.hash = "";
    history.replaceState(null, "", url.toString());
  }

  // Breadcrumb
  const crumb =
    route.name === "upload"
      ? "رفع"
      : route.name === "configure"
      ? "الإعداد"
      : route.name === "progress"
      ? "التنفيذ"
      : route.name === "result"
      ? "النتيجة"
      : "";

  const CrumbSep = isRtl ? ChevronLeft : ChevronRight;

  return (
    <div className="grid min-h-screen md:grid-cols-[280px_1fr] xl:grid-cols-[320px_1fr]">
      <Sidebar
        jobs={jobs}
        currentJobId={activeJob?.id}
        onNewJob={handleNewJob}
        onPickJob={(j) => resumeJob(j.id)}
      />

      <div className="grid grid-rows-[auto_1fr] min-w-0 overflow-hidden">
        <div className="h-14 sticky top-0 z-20 border-b border-line bg-[oklch(0.13_0.005_250/0.72)] backdrop-blur-md flex items-center justify-between px-6 lg:px-12">
          <div className="flex items-center gap-3 text-[13px] text-paper-2">
            <span>س‑ترجم</span>
            <CrumbSep className="h-3.5 w-3.5 text-paper-4" />
            <span className="text-paper-0">{crumb}</span>
            {(route.name === "progress" || route.name === "result") && activeJob && (
              <>
                <CrumbSep className="h-3.5 w-3.5 text-paper-4" />
                <span className="font-mono text-[11px] text-paper-3" dir="ltr">
                  {activeJob.id}
                </span>
              </>
            )}
          </div>
          <div className="flex items-center gap-2">
            <Button
              variant="ghost"
              size="sm"
              onClick={() => setSettingsOpen(true)}
            >
              <Settings className="h-4 w-4" />
              الإعدادات
            </Button>
          </div>
        </div>

        <div className="overflow-y-auto p-6 lg:p-12">
          {route.name === "upload" && (
            <UploadScreen
              stagedFiles={stagedFiles}
              onAdd={handleAddFile}
              onRemove={handleRemoveFile}
              onContinue={() => setRoute({ name: "configure" })}
              onResumeById={(id) => resumeJob(id)}
              configured={configured}
              onOpenSettings={() => setSettingsOpen(true)}
              isRtl={isRtl}
            />
          )}
          {route.name === "configure" && stagedFiles.length > 0 && (
            <ConfigureScreen
              cfg={cfg}
              setCfg={setCfg}
              stagedFiles={stagedFiles}
              onBack={() => setRoute({ name: "upload" })}
              onStart={handleStartJob}
              isRtl={isRtl}
              starting={starting}
            />
          )}
          {route.name === "configure" && stagedFiles.length === 0 && (
            <EmptyState
              title="لا توجد ملفات للإعداد"
              sub="ابدأ برفع مستند أولاً."
              cta="ارفع مستندًا"
              onCta={() => setRoute({ name: "upload" })}
            />
          )}
          {route.name === "progress" && activeJob && (
            <ProgressScreen
              job={activeJob}
              fileName={activeFileName}
              logs={logs}
              preview={activePreview}
              onCancel={handleCancelJob}
              isRtl={isRtl}
            />
          )}
          {route.name === "result" && activeJob && (
            <ResultScreen
              job={activeJob}
              preview={activePreview}
              onAnother={handleNewJob}
              onOpenSettings={() => setSettingsOpen(true)}
            />
          )}
        </div>
      </div>

      <SettingsModal
        open={settingsOpen}
        onOpenChange={setSettingsOpen}
        cfg={cfg}
        setCfg={(c) => {
          setCfg(c);
          // Save tweaks alongside in case we later add them
          saveTweaks(tweaks);
        }}
        enforceGate={!configured}
      />
    </div>
  );

  // silence unused-var warning without actually removing `setTweaksState`
  void setTweaksState;
}

function EmptyState({
  title,
  sub,
  cta,
  onCta,
}: {
  title: string;
  sub: string;
  cta: string;
  onCta: () => void;
}) {
  return (
    <div className="grid place-items-center min-h-[60vh]">
      <div className="rounded-lg border border-line p-9 text-center max-w-md bg-ink-1">
        <h3 className="text-lg font-bold">{title}</h3>
        <p className="text-paper-2 mt-2">{sub}</p>
        <Button variant="accent" className="mt-4" onClick={onCta}>
          {cta}
        </Button>
      </div>
    </div>
  );
}

export default function App() {
  return (
    <ToastProvider>
      <AppInner />
    </ToastProvider>
  );
}
