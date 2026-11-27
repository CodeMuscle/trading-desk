// Relative, because Vite proxies /api to the engine on 127.0.0.1:8787. The
// page and its data therefore share an origin and no CORS config is needed.
const BASE = "/api";

/** 1500 4h bars is roughly eight months.

The old default of 300 was eight weeks, which is why the chart opened on seven
weeks of history and offered no way to see further back. The engine pages past
the exchange's 1000-bar cap, so the ceiling here is a UI choice rather than a
venue limit. */
export const DEFAULT_BARS = 1500;
export const BAR_CHOICES = [500, 1500, 3000, 6000];

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

export type Zone = {
  kind: "demand" | "supply";
  top: number;
  bottom: number;
  start: number;
  end: number;
  departure_atr: number;
  base_bars: number;
  tested: boolean;
  age_bars: number;
};

export type MetricResponse = {
  name: string;
  state: MetricState;
  reason?: string;
  stale: boolean;
  points: Point[];
  /** dict_of_series metrics: macd, imbalance_bands, confirmed_swing_levels. */
  series?: Record<string, Point[]>;
  /** scalars metrics: value_area, fibonacci_levels. */
  scalars?: Record<string, number>;
  /** zones metrics: supply_demand_zones. */
  zones?: Zone[];
  fresh?: number;
  /** profile metrics: volume_profile returns price/volume buckets. */
  profile?: { price: number; volume: number }[];
  poc?: number;
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

export type Account = {
  name: string;
  venue: string;
  equity: number;
  starting_equity: number;
  return_pct: number;
  position_open: boolean;
  last_bar: string | null;
};

export type Trade = {
  entry_index: string;
  exit_index: string;
  symbol: string;
  side: "LONG" | "SHORT";
  outcome: "stop" | "target" | "end_of_data";
  entry: number;
  exit: number;
  stop: number;
  target: number;
  net: number;
  r_multiple: number;
  mae: number;
  mfe: number;
  equity_after: number;
  lag_seconds: number | null;
};

export type JournalResponse = {
  account: string;
  trades: Trade[];
  summary: {
    count: number;
    net: number;
    /** null rather than 0 on an empty journal: a win rate of zero on no trades
     *  is a claim, and the absence of one is the honest value. */
    win_rate: number | null;
    expectancy_r: number | null;
  };
};

export type Refusal = { bar_index: string; symbol: string; reason: string };

export const api = {
  health: () => get<{ venue: string; capabilities: string[] }>("/health"),
  registry: () => get<{ metrics: MetricSpec[] }>("/metrics"),
  ohlcv: (symbol: string, timeframe: string, limit = DEFAULT_BARS) =>
    get<{ bars: Bar[]; venue: string; symbol: string }>("/ohlcv", { symbol, timeframe, limit }),
  metric: (
    name: string,
    symbol: string,
    timeframe: string,
    extra: Record<string, number> = {},
    limit = DEFAULT_BARS,
  ) => get<MetricResponse>(`/metric/${name}`, { symbol, timeframe, limit, ...extra }),
  accounts: () => get<{ accounts: Account[] }>("/accounts"),
  journal: (name: string) => get<JournalResponse>(`/journal/${name}`),
  refusals: (name: string) =>
    get<{ refusals: Refusal[] }>(`/refusals/${name}`),
};
