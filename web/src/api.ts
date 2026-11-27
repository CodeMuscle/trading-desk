// Relative, because Vite proxies /api to the engine on 127.0.0.1:8787. The
// page and its data therefore share an origin and no CORS config is needed.
/** Demo mode: read frozen JSON instead of calling the engine.
 *
 * The engine binds loopback and always will, so a hosted page cannot reach it.
 * Rather than pretend otherwise, the deployed build ships a snapshot and reads
 * it from disk. Switched on at build time by VITE_DEMO rather than sniffed at
 * runtime, so a local dev server can never silently serve stale frozen data
 * while looking live.
 *
 * The snapshot carries no accounts, journal, refusals or risk limits. The
 * paper desk therefore renders its empty state, which is the honest display of
 * "this deployment has no paper desk" rather than a fabricated one.
 */
const DEMO = import.meta.env.VITE_DEMO === "1";

const BASE = "/api";

/** Everything the venue has.

The engine pages to the start of listing and clamps the window at the epoch, so
this is a real request rather than a large number that happens to work: BTC/USDT
returns 20,320 bars on 4h and 3,390 on 1d, both back to August 2017. The cache
tops up its tail afterwards, so the full fetch is paid once rather than on every
bar close. */
export const FULL_HISTORY = 60000;
export const DEFAULT_BARS = DEMO ? 1500 : FULL_HISTORY;

export const BAR_CHOICES: [string, number][] = DEMO
  ? // The snapshot is a fixed 1500 bars per symbol and timeframe, so offering
    // ranges the demo cannot serve would be three buttons that error.
    [["1500 bars", 1500]]
  : [
      ["1y", 2190],
      ["3y", 6570],
      ["all", FULL_HISTORY],
    ];

/** Validation found ETH and SOL stronger than BTC, and they were unreachable
 *  while the symbol was a constant. */
export const SYMBOLS = ["BTC/USDT", "ETH/USDT", "SOL/USDT"];

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



function slug(symbol: string, timeframe: string): string {
  return `${symbol.replace("/", "-")}__${timeframe}`;
}

async function demoGet<T>(file: string): Promise<T> {
  const response = await fetch(`${import.meta.env.BASE_URL}demo/${file}.json`);
  if (!response.ok) {
    throw new Error(
      `this demo has no snapshot for ${file}. The hosted build carries a fixed ` +
        `set of symbols and timeframes; run the desk locally for the rest.`,
    );
  }
  return (await response.json()) as T;
}

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
  demo: DEMO,
  health: () =>
    DEMO
      ? Promise.resolve({ venue: "binance", capabilities: ["ohlcv"] })
      : get<{ venue: string; capabilities: string[] }>("/health"),
  registry: () =>
    DEMO
      ? demoGet<{ metrics: MetricSpec[] }>("metrics")
      : get<{ metrics: MetricSpec[] }>("/metrics"),
  ohlcv: (symbol: string, timeframe: string, limit = DEFAULT_BARS) =>
    DEMO
      ? demoGet<{ bars: Bar[]; venue: string; symbol: string }>(
          `ohlcv__${slug(symbol, timeframe)}`,
        )
      : get<{ bars: Bar[]; venue: string; symbol: string }>("/ohlcv", {
          symbol,
          timeframe,
          limit,
        }),
  metric: (
    name: string,
    symbol: string,
    timeframe: string,
    extra: Record<string, number> = {},
    limit = DEFAULT_BARS,
    /** Points to return. The panel shows one reading per metric, so it asks
     *  for one point; an overlay that draws a line omits this. */
    tail?: number,
  ) =>
    DEMO
      ? demoGet<MetricResponse>(
          // The overlays are the only metrics the demo stores as full series;
          // every other reading is frozen at its last point, exactly as the
          // live panel requests it.
          name === "ema" && extra.length
            ? `ema${extra.length}__${slug(symbol, timeframe)}`
            : name === "session_vwap" && !tail
              ? `vwap__${slug(symbol, timeframe)}`
              : `metric__${name}__${slug(symbol, timeframe)}`,
        )
      : get<MetricResponse>(`/metric/${name}`, {
      symbol,
      timeframe,
      limit,
      ...extra,
      ...(tail ? { tail } : {}),
    }),
  // No paper desk in the demo. An empty list renders the panel's own empty
  // state, which says so, rather than inventing an account.
  accounts: () =>
    DEMO
      ? Promise.resolve({ accounts: [] as Account[] })
      : get<{ accounts: Account[] }>("/accounts"),
  journal: (name: string) => get<JournalResponse>(`/journal/${name}`),
  refusals: (name: string) =>
    get<{ refusals: Refusal[] }>(`/refusals/${name}`),
};
