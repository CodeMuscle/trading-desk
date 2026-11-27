import { createChart, darkTheme, lightTheme, type Chart, type SeriesApi } from "openalgo-charts";

import { BAR_CHOICES, DEFAULT_BARS, api, type Bar, type MetricResponse, type Point } from "./api";

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

/** Render whatever shape the engine sent.

The three payload shapes the registry produces are series, dict_of_series,
scalars and profile, and only the first two were handled. A scalars metric
therefore fell through to `points.at(-1)` on an empty array and printed "-",
which is the same thing an unavailable metric prints. In a project whose whole
premise is that unavailable is honest signalling, a UI that renders "I have
four numbers for you" identically to "this venue cannot answer" breaks the
contract at the only place anyone looks at it. */
function describe(response: MetricResponse): string {
  if (response.scalars) {
    return Object.entries(response.scalars)
      .map(([name, value]) => `${name} ${fmt(value)}`)
      .join("  ");
  }
  if (response.profile) {
    const poc = response.poc;
    return `${response.profile.length} levels${poc ? `  poc ${fmt(poc)}` : ""}`;
  }
  if (response.series) {
    // The last value of each sub-series, not its length. Printing the length
    // produced "macd:300 signal:300 hist:300", three identical bar counts
    // wearing the names of three different numbers.
    return Object.entries(response.series)
      .map(([name, points]) => {
        const last = points.at(-1)?.value;
        return `${name} ${typeof last === "number" ? fmt(last) : String(last ?? "-")}`;
      })
      .join("  ");
  }
  const last = response.points.at(-1)?.value;
  if (typeof last === "number") return fmt(last);
  if (typeof last === "boolean") return last ? "true" : "false";
  return String(last ?? "-");
}

function fmt(value: number): string {
  const magnitude = Math.abs(value);
  const digits = magnitude >= 1000 ? 0 : magnitude >= 1 ? 2 : 4;
  return value.toLocaleString("en-US", { maximumFractionDigits: digits });
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
    value.textContent = describe(response);
    if (response.stale) {
      // A badge rather than a dimmer colour. Stale was previously signalled by
      // opacity alone, which is unreadable next to the ordinary low-contrast
      // values this panel is full of, and it was the only on-screen warning
      // that the chart was serving two-day-old candles.
      row.classList.add("is-stale");
      const flag = document.createElement("span");
      flag.className = "flag";
      flag.textContent = "STALE";
      flag.title = "Computed on a frame older than twice its timeframe.";
      value.append(" ", flag);
    }
  } else {
    value.classList.add("na");
    value.textContent = response.state === "unavailable" ? "unavailable" : "error";
    const why = document.createElement("span");
    why.className = "why";
    why.textContent = response.reason ?? "";
    value.append(" ", why);
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
  const state = mount(container);

  async function load(timeframe: string, limit: number): Promise<void> {
    const [{ bars, venue }, e20, e50, e200, vwap] = await Promise.all([
      api.ohlcv(symbol, timeframe, limit),
      api.metric("ema", symbol, timeframe, { length: 20 }, limit),
      api.metric("ema", symbol, timeframe, { length: 50 }, limit),
      api.metric("ema", symbol, timeframe, { length: 200 }, limit),
      api.metric("session_vwap", symbol, timeframe, {}, limit),
    ]);

    state.candles.setData(bars as Bar[]);
    overlay(state, "ema200", e200, token("--ink-dim") || "#555");
    overlay(state, "ema50", e50, token("--accent") || "#D98A3D");
    overlay(state, "ema20", e20, token("--info") || "#5B9BD5");
    overlay(state, "vwap", vwap, token("--annotate") || "#9B8BD9");

    const last = bars.at(-1);
    const first = bars[0];
    if (last && first) {
      const change = ((last.close - first.close) / first.close) * 100;
      const days = Math.round((last.time - first.time) / 86400);
      header.textContent =
        `${symbol} · ${timeframe} · ${venue} · ` +
        `${last.close.toLocaleString("en-US")} ` +
        `(${change >= 0 ? "+" : ""}${change.toFixed(2)}% over ${bars.length} bars, ${days}d)`;
    }

    const { metrics } = await api.registry();
    const responses = await Promise.all(
      metrics.map((spec) => api.metric(spec.name, symbol, timeframe, {}, limit)),
    );
    panel.replaceChildren(...responses.map(metricRow));
  }

  // Range and timeframe are controls rather than constants. The chart opened on
  // 300 bars with no way to ask for more, which on 4h is seven weeks: the
  // engine pages past the exchange's 1000-bar cap, so the shallow history was
  // a UI default and not a venue limit.
  const controls = document.getElementById("controls");
  if (controls) {
    const timeframes = ["1h", "4h", "1d"];
    let timeframe = "4h";
    let limit = DEFAULT_BARS;

    const render = () => {
      controls.replaceChildren();
      for (const value of timeframes) {
        controls.append(chip(value, value === timeframe, async () => {
          timeframe = value;
          render();
          await load(timeframe, limit);
        }));
      }
      const spacer = document.createElement("span");
      spacer.className = "csep";
      controls.append(spacer);
      for (const value of BAR_CHOICES) {
        controls.append(chip(`${value}`, value === limit, async () => {
          limit = value;
          render();
          await load(timeframe, limit);
        }));
      }
    };
    render();
  }

  await load("4h", DEFAULT_BARS);
}

function chip(label: string, active: boolean, onClick: () => void): HTMLElement {
  const button = document.createElement("button");
  button.className = active ? "chip is-active" : "chip";
  button.type = "button";
  button.textContent = label;
  button.addEventListener("click", onClick);
  return button;
}
