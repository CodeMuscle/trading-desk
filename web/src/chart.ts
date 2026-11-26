import { createChart, darkTheme, lightTheme, type Chart, type SeriesApi } from "openalgo-charts";

import { api, type Bar, type MetricResponse, type Point } from "./api";

export type DeskState = {
  chart: Chart;
  candles: SeriesApi;
  overlays: Map<string, SeriesApi>;
};

/** Follows design/tokens.css, which is dark-first and switches only on an
 *  explicit data-theme. Reading the OS preference here instead would put a
 *  light chart on a dark page for anyone whose OS is set to light. */
function isDark(): boolean {
  return document.documentElement.dataset.theme !== "light";
}

function token(name: string): string {
  return getComputedStyle(document.documentElement).getPropertyValue(name).trim();
}

export function mount(container: HTMLElement): DeskState {
  const chart = createChart(container, {
    theme: isDark() ? darkTheme : lightTheme,
  });
  const candles = chart.addSeries("candlestick");
  return { chart, candles, overlays: new Map() };
}

/** Numeric points only. A boolean or string metric is not a line. */
function lineData(response: MetricResponse): { time: number; value: number }[] {
  return response.points
    .filter((p): p is Point & { value: number } => typeof p.value === "number")
    .map((p) => ({ time: p.time, value: p.value }));
}

export function overlay(
  state: DeskState,
  key: string,
  response: MetricResponse,
  colour: string,
): void {
  const data = lineData(response);
  if (data.length === 0) return;

  let series = state.overlays.get(key);
  if (!series) {
    series = state.chart.addSeries("line");
    state.overlays.set(key, series);
  }
  series.applyOptions({ color: colour, lineWidth: 1.5 });
  series.setData(data);
}

/** One row of the metrics panel, rendering all three result states distinctly. */
export function metricRow(response: MetricResponse): HTMLElement {
  const row = document.createElement("div");
  row.className = "mrow";

  const key = document.createElement("span");
  key.className = "mk";
  key.textContent = response.name;

  const value = document.createElement("span");
  value.className = "mv";

  if (response.state === "ok") {
    if (response.series) {
      const counts = Object.entries(response.series)
        .map(([name, points]) => `${name}:${points.length}`)
        .join(" ");
      value.textContent = counts;
    } else {
      const last = response.points.at(-1)?.value;
      value.textContent =
        typeof last === "number"
          ? last.toLocaleString("en-US", { maximumFractionDigits: 2 })
          : String(last ?? "-");
    }
    if (response.stale) value.classList.add("stale");
  } else {
    value.classList.add("na");
    value.textContent = `${response.state} · ${response.reason ?? ""}`;
    value.title = response.reason ?? "";
  }

  row.append(key, value);
  return row;
}

export async function boot(
  container: HTMLElement,
  panel: HTMLElement,
  header: HTMLElement,
): Promise<void> {
  const symbol = "BTC/USDT";
  const timeframe = "4h";

  const state = mount(container);

  const [{ bars, venue }, e20, e50, e200, vwap] = await Promise.all([
    api.ohlcv(symbol, timeframe),
    api.metric("ema", symbol, timeframe, { length: 20 }),
    api.metric("ema", symbol, timeframe, { length: 50 }),
    api.metric("ema", symbol, timeframe, { length: 200 }),
    api.metric("session_vwap", symbol, timeframe),
  ]);

  state.candles.setData(bars as Bar[]);

  overlay(state, "ema200", e200, token("--ink-dim") || "#555");
  overlay(state, "ema50", e50, token("--accent") || "#D98A3D");
  overlay(state, "ema20", e20, token("--info") || "#5B9BD5");
  overlay(state, "vwap", vwap, token("--annotate") || "#9B8BD9");

  const last = bars.at(-1);
  if (last) {
    const first = bars[0];
    const change = ((last.close - first.close) / first.close) * 100;
    header.textContent =
      `${symbol} · ${timeframe} · ${venue} · ` +
      `${last.close.toLocaleString("en-US")} (${change >= 0 ? "+" : ""}${change.toFixed(2)}%)`;
  }

  const { metrics } = await api.registry();
  const responses = await Promise.all(
    metrics.map((spec) => api.metric(spec.name, symbol, timeframe)),
  );
  panel.replaceChildren(...responses.map(metricRow));
}
