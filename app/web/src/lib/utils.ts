import { clsx, type ClassValue } from "clsx";
import { twMerge } from "tailwind-merge";

export function cn(...inputs: ClassValue[]) {
  return twMerge(clsx(inputs));
}

export function fmtSize(kb: number): string {
  if (kb < 1024) return `${kb.toFixed(0)} ك.ب`;
  return `${(kb / 1024).toFixed(1)} م.ب`;
}

export function fmtDur(sec: number): string {
  if (sec < 60) return `${Math.round(sec)} ث`;
  const m = Math.floor(sec / 60);
  const s = Math.round(sec % 60);
  return `${m} د ${s.toString().padStart(2, "0")} ث`;
}

export function relTime(iso: string): string {
  const now = Date.now();
  const then = new Date(iso).getTime();
  const diff = Math.max(0, now - then);
  const min = 60_000, hr = 60 * min, day = 24 * hr;
  if (diff < hr) return `قبل ${Math.max(1, Math.round(diff / min))} دقيقة`;
  if (diff < day) return `قبل ${Math.round(diff / hr)} ساعة`;
  if (diff < 7 * day) return `قبل ${Math.round(diff / day)} يوم`;
  return new Date(iso).toISOString().slice(0, 10);
}

export function genJobId(): string {
  const pool = "0123456789ABCDEFGHJKMNPQRSTVWXYZ";
  let s = "tr_";
  for (let i = 0; i < 10; i++) s += pool[Math.floor(Math.random() * pool.length)];
  return s;
}
