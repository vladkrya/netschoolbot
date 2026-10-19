import { state } from "./state";
import type { Week } from "./types";
import { iso } from "./util";

export class ApiError extends Error {
  constructor(
    message: string,
    readonly status: number,
  ) {
    super(message);
  }
}

export async function api<T>(path: string, init: RequestInit = {}): Promise<T> {
  const response = await fetch(path, {
    credentials: "same-origin",
    headers: { "Content-Type": "application/json" },
    ...init,
  });
  const data = (await response.json().catch(() => ({}))) as { detail?: string };
  if (!response.ok) throw new ApiError(data.detail || `Ошибка ${response.status}`, response.status);
  return data as T;
}

// ---------- недели: stale-while-revalidate ----------
// Неделя сразу рисуется из кэша (память → sessionStorage), а свежая версия
// догружается в фоне. Так листание недель мгновенное, а сервер «Сетевого
// города» не дёргается чаще, чем раз в FRESH_MS на неделю.

const FRESH_MS = 60_000;
const memory = new Map<string, { at: number; week: Week }>();
const inflight = new Map<string, Promise<Week>>();

function storageKey(start: string) {
  return `week:${start}`;
}

export function cachedWeek(start: Date): { week: Week; fresh: boolean } | null {
  if (state.demo) return null;
  const key = iso(start);
  let hit = memory.get(key);
  if (!hit) {
    try {
      const raw = sessionStorage.getItem(storageKey(key));
      if (raw) memory.set(key, (hit = JSON.parse(raw) as { at: number; week: Week }));
    } catch {
      /* sessionStorage недоступен */
    }
  }
  return hit ? { week: hit.week, fresh: Date.now() - hit.at < FRESH_MS } : null;
}

export async function fetchWeek(start: Date): Promise<Week> {
  if (state.demo) {
    const { demoWeek } = await import("./demo");
    return demoWeek(start);
  }
  const key = iso(start);
  // Два одновременных запроса одной недели (сетка и карточки) — один поход на сервер.
  const pending = inflight.get(key);
  if (pending) return pending;
  const request = api<Week>(`/api/week?start=${key}`)
    .then((week) => {
      const entry = { at: Date.now(), week };
      memory.set(key, entry);
      try {
        sessionStorage.setItem(storageKey(key), JSON.stringify(entry));
      } catch {
        /* переполнено — останется в памяти */
      }
      return week;
    })
    .finally(() => inflight.delete(key));
  inflight.set(key, request);
  return request;
}

/** Неделя для показа: из кэша, если он свежий, иначе с сервера. */
export async function loadWeek(start: Date): Promise<Week> {
  const hit = cachedWeek(start);
  return hit?.fresh ? hit.week : fetchWeek(start);
}

export function forgetWeeks(): void {
  memory.clear();
  try {
    for (const key of Object.keys(sessionStorage))
      if (key.startsWith("week:")) sessionStorage.removeItem(key);
  } catch {
    /* ignore */
  }
}
