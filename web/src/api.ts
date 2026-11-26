// Relative, because Vite proxies /api to the engine on 127.0.0.1:8787. The
// page and its data therefore share an origin and no CORS config is needed.
const BASE = "/api";

export type Bar = {
  time: number;
  open: number;
  high: number;
  low: number;
  close: number;
  volume: number;
};

export type Point = { time: number; value: number | boolean | string };

export type MetricState = "ok" | "unavailable" | "error";

export type MetricResponse = {
  name: string;
  state: MetricState;
  reason?: string;
  stale: boolean;
  points: Point[];
  series?: Record<string, Point[]>;
};

export type MetricSpec = {
  name: string;
  requires: string[];
  returns: string;
  defaults: Record<string, number>;
};

async function get<T>(path: string, params: Record<string, string | number> = {}): Promise<T> {
  const url = new URL(BASE + path, window.location.origin);
  for (const [key, value] of Object.entries(params)) {
    url.searchParams.set(key, String(value));
  }
  const response = await fetch(url);
  if (!response.ok) {
    throw new Error(`${path} failed: ${response.status} ${await response.text()}`);
  }
  return (await response.json()) as T;
}

export const api = {
  health: () => get<{ venue: string; capabilities: string[] }>("/health"),
  registry: () => get<{ metrics: MetricSpec[] }>("/metrics"),
  ohlcv: (symbol: string, timeframe: string, limit = 300) =>
    get<{ bars: Bar[]; venue: string; symbol: string }>("/ohlcv", { symbol, timeframe, limit }),
  metric: (
    name: string,
    symbol: string,
    timeframe: string,
    extra: Record<string, number> = {},
  ) => get<MetricResponse>(`/metric/${name}`, { symbol, timeframe, limit: 300, ...extra }),
};
