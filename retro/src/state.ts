import type { Me, Week } from "./types";
import { mondayOf, store } from "./util";

export const state = {
  demo: store.get("demo", false),
  weekStart: mondayOf(new Date()),
  mode: "week" as "week" | "periods",
  me: null as Me | null,
  lastWeek: null as Week | null,
};
