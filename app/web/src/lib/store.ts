// Persistent config + tweaks helpers. API keys live in localStorage only
// on this device — the server never persists them.

import { useSyncExternalStore } from "react";
import type { SharedKey } from "./api";

export type Shape = "translated" | "original" | "stacked" | "side-by-side";
export type Accent = "green" | "amber" | "blue" | "coral" | "paper";
export type Density = "compact" | "cozy" | "roomy";

/** "shared" → use the server's shared API key (cfg.apiKey is ignored).
 *  "own"    → use cfg.apiKey as before.
 *  null     → user hasn't picked yet; configure screen shows the mode gate
 *             when a shared key is available. */
export type KeyMode = "shared" | "own" | null;

export interface Config {
  target: string; // lang code
  shape: Shape;
  providerId: string;
  apiKey: string;
  apiBase: string;
  model: string;
  temperature: string;
  chunkSize: string;
  glossary: string;
  keyMode: KeyMode;
}

export const DEFAULT_CONFIG: Config = {
  target: "ar",
  shape: "side-by-side",
  providerId: "anthropic",
  apiKey: "",
  apiBase: "https://api.anthropic.com/v1",
  model: "claude-sonnet-4-6",
  temperature: "0.2",
  chunkSize: "1200",
  glossary: "",
  keyMode: null,
};

export interface Tweaks {
  accent: Accent;
  density: Density;
}

export const DEFAULT_TWEAKS: Tweaks = {
  accent: "green",
  density: "cozy",
};

const CFG_KEY = "s-trans:cfg:v2";
const TWK_KEY = "s-trans:tweaks:v2";
const ROUTE_KEY = "s-trans:route:v2";

export function loadConfig(): Config {
  try {
    const raw = localStorage.getItem(CFG_KEY);
    if (!raw) return DEFAULT_CONFIG;
    return { ...DEFAULT_CONFIG, ...JSON.parse(raw) };
  } catch {
    return DEFAULT_CONFIG;
  }
}

export function saveConfig(c: Config) {
  try { localStorage.setItem(CFG_KEY, JSON.stringify(c)); } catch { /* ignore */ }
}

export function loadTweaks(): Tweaks {
  try {
    const raw = localStorage.getItem(TWK_KEY);
    if (!raw) return DEFAULT_TWEAKS;
    return { ...DEFAULT_TWEAKS, ...JSON.parse(raw) };
  } catch {
    return DEFAULT_TWEAKS;
  }
}

export function saveTweaks(t: Tweaks) {
  try { localStorage.setItem(TWK_KEY, JSON.stringify(t)); } catch { /* ignore */ }
}

export function loadRoute(): { name: string; jobId?: string } {
  try {
    const raw = localStorage.getItem(ROUTE_KEY);
    if (!raw) return { name: "upload" };
    return JSON.parse(raw);
  } catch {
    return { name: "upload" };
  }
}

export function saveRoute(r: { name: string; jobId?: string }) {
  try { localStorage.setItem(ROUTE_KEY, JSON.stringify(r)); } catch { /* ignore */ }
}

// The provider this deploy has a shared API key for, populated once at app
// boot from GET /api/config. Null means the user must bring their own key.
let _sharedKey: SharedKey | null = null;

export function setSharedKey(s: SharedKey | null): void {
  _sharedKey = s;
  notifyStore();
}

export function getSharedKey(): SharedKey | null {
  return _sharedKey;
}

/** Does the user's current config cover the shared key (i.e. they've picked
 *  shared mode AND the server still advertises a key for their provider)? */
export function usingSharedKey(c: Config): boolean {
  if (c.keyMode !== "shared") return false;
  return !!_sharedKey && _sharedKey.provider === c.providerId;
}

/** Does the config have the minimum needed to start a translation? */
export function isConfigured(c: Config): boolean {
  if (!c.model.trim()) return false;
  if (c.providerId === "ollama") return true; // keyless
  if (usingSharedKey(c)) return true;          // server covers the key
  return !!c.apiKey.trim();
}

// Tiny hook to force re-render when localStorage changes in the same tab.
// Useful for cross-component sync (e.g. settings modal → upload screen).
type Listener = () => void;
const listeners = new Set<Listener>();
export function notifyStore() {
  listeners.forEach((l) => l());
}
export function useStoreSubscription(): number {
  return useSyncExternalStore(
    (cb) => {
      listeners.add(cb);
      return () => listeners.delete(cb);
    },
    () => listeners.size,
    () => 0
  );
}
