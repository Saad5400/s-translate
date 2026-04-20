// Thin wrapper around the FastAPI backend at /api.
// API keys never touch localStorage; they're passed per-request only.

export type BackendJobStatus = "queued" | "running" | "done" | "failed";

export interface JobMeta {
  id: string;
  status: BackendJobStatus;
  progress: number;
  message: string;
  target_lang: string;
  provider: string;
  model: string;
  output_mode: string;
  input_name: string;
  output_name: string;
  created_at: number;
  updated_at: number;
  error: string | null;
  download_url?: string;
}

export interface CreateJobInput {
  file: File;
  target_lang: string;
  provider: string;
  model: string;
  api_key: string;
  api_base?: string;
  temperature: number;
  output_mode: string;
  max_chunk_tokens: number;
}

export async function createJob(input: CreateJobInput): Promise<{ id: string }> {
  const fd = new FormData();
  fd.append("file", input.file);
  fd.append("target_lang", input.target_lang);
  fd.append("provider", input.provider);
  fd.append("model", input.model);
  fd.append("api_key", input.api_key);
  if (input.api_base) fd.append("api_base", input.api_base);
  fd.append("temperature", String(input.temperature));
  fd.append("output_mode", input.output_mode);
  fd.append("max_chunk_tokens", String(input.max_chunk_tokens));

  const r = await fetch("/api/jobs", { method: "POST", body: fd });
  if (!r.ok) {
    const text = await r.text().catch(() => "");
    throw new Error(`${r.status}: ${text || r.statusText}`);
  }
  const data = await r.json();
  return { id: data.id as string };
}

export async function getJob(id: string): Promise<JobMeta | null> {
  const r = await fetch(`/api/jobs/${encodeURIComponent(id)}`);
  if (r.status === 404) return null;
  if (!r.ok) throw new Error(`${r.status} ${r.statusText}`);
  return (await r.json()) as JobMeta;
}

export async function listJobs(limit = 50): Promise<JobMeta[]> {
  const r = await fetch(`/api/jobs?limit=${limit}`);
  if (!r.ok) throw new Error(`${r.status} ${r.statusText}`);
  const data = await r.json();
  return (data.jobs ?? []) as JobMeta[];
}

export async function deleteJob(id: string): Promise<void> {
  await fetch(`/api/jobs/${encodeURIComponent(id)}`, { method: "DELETE" });
}

export function downloadUrl(id: string): string {
  return `/api/jobs/${encodeURIComponent(id)}/download`;
}

/** Poll a job until it reaches a terminal state. Calls `onUpdate` whenever
 *  the meta changes. Returns the final JobMeta. */
export async function pollJob(
  id: string,
  opts: {
    onUpdate?: (m: JobMeta) => void;
    intervalMs?: number;
    signal?: AbortSignal;
  } = {}
): Promise<JobMeta> {
  const { onUpdate, intervalMs = 1200, signal } = opts;
  let last: JobMeta | null = null;
  while (true) {
    if (signal?.aborted) throw new DOMException("Aborted", "AbortError");
    const m = await getJob(id);
    if (!m) throw new Error(`job ${id} not found`);
    if (
      !last ||
      last.status !== m.status ||
      last.progress !== m.progress ||
      last.message !== m.message
    ) {
      onUpdate?.(m);
    }
    last = m;
    if (m.status === "done" || m.status === "failed") return m;
    await new Promise((r) => setTimeout(r, intervalMs));
  }
}
