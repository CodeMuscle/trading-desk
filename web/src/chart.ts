import {
  createChart,
  darkTheme,
  lightTheme,
  type Chart,
  type SeriesApi,
  type SeriesMarker,
  type SeriesMarkers,
  type PriceLine,
} from "openalgo-charts";

import {
  BAR_CHOICES,
  DEFAULT_BARS,
  api,
  type Bar,
  type MetricResponse,
  type Point,
  type Trade,
  type Zone,
  SYMBOLS,
} from "./api";
import { renderDesk } from "./desk";
import {
  DEFAULT_VIEW,
  renderRail,
  type SeriesKind,
  type ViewState,
} from "./rail";

export type DeskState = {
  chart: Chart;
  candles: SeriesApi;
  overlays: Map<string, SeriesApi>;
  /** Created once and reused. setData clears the series, not this primitive,
   *  so markers survive a timeframe change and are reapplied explicitly. */
  markers: SeriesMarkers;
  /** Tracked so a reload removes the previous set rather than stacking a new
   *  one on top of it, which is how a chart ends up with forty lines on it. */
  zoneLines: PriceLine[];
  fibLines: PriceLine[];
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

export function mount(container: HTMLElement, kind: SeriesKind = "candlestick"): DeskState {
  const chart = createChart(container, {
    theme: isDark() ? darkTheme : lightTheme,
  });
  const candles = chart.addSeries(kind);
  return {
    chart, candles, overlays: new Map(),
    markers: candles.createMarkers(), zoneLines: [], fibLines: [],
  };
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
  if (response.zones) {
    const fresh = response.fresh ?? response.zones.filter((z) => !z.tested).length;
    return `${response.zones.length} zones  ${fresh} fresh`;
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
    const reading = describe(response);
    value.textContent = reading;
    // A multi-value reading stacks under its own label. Side by side, the
    // value column takes the whole row and the label collapses to nothing, so
    // fibonacci_levels and value_area rendered as unlabelled walls of numbers.
    if (reading.length > 22) row.classList.add("is-wide");
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

/** Supply and demand zones as price lines bounding each band.
 *
 * Untested zones are drawn solid and tested ones dashed, because a zone price
 * has already traded back through has had its resting orders filled and is a
 * weaker argument than a fresh one. They are not hidden: how a level failed is
 * information the next decision needs.
 *
 * Only the strongest few are drawn. A chart carrying every qualifying base is
 * a chart where no level stands out, which is the failure mode that makes this
 * kind of study useless in practice.
 */
export function drawZones(state: DeskState, zones: Zone[], keep = 6): void {
  for (const line of state.zoneLines) state.chart.removePrimitive(line);
  state.zoneLines = [];

  // Ranked fresh first, then by recency, and only then by strength. Sorting by
  // strength alone surfaces whichever ancient base happened to precede the
  // largest move in the window, which is a fact about history rather than a
  // level anyone is trading against. A zone price has already traded back
  // through has had its resting orders filled, so it ranks below any untested
  // one however violent its departure was.
  const strongest = [...zones]
    .sort((a, b) => {
      if (a.tested !== b.tested) return a.tested ? 1 : -1;
      if (a.age_bars !== b.age_bars) return a.age_bars - b.age_bars;
      return b.departure_atr - a.departure_atr;
    })
    .slice(0, keep);

  let n = 0;
  for (const zone of strongest) {
    const colour =
      zone.kind === "demand" ? token("--pos") || "#2FBF84" : token("--neg") || "#E8524F";
    for (const price of [zone.top, zone.bottom]) {
      state.zoneLines.push(
        state.chart.addPriceLine({
          price,
          color: colour,
          lineWidth: 1,
          dashed: zone.tested,
          id: `zone-${n++}`,
          // Only the band's upper edge is labelled. Labelling both puts two
          // pills a few pixels apart saying the same thing.
          label:
            price === zone.top
              ? `${zone.kind} ${zone.departure_atr.toFixed(1)}ATR${
                  zone.tested ? " tested" : ""
                }`
              : undefined,
        }),
      );
    }
  }
}

/** Fibonacci retracements as levels on the price scale.
 *
 * Drawn in the annotation colour rather than a data colour, because these are
 * lines a person chose to look at and not a measurement of the market. Mixing
 * them into the green and red reserved for price would make a drawn level look
 * like a reading.
 */
export function drawFib(state: DeskState, levels: Record<string, number>): void {
  for (const line of state.fibLines) state.chart.removePrimitive(line);
  state.fibLines = [];

  const colour = token("--annotate") || "#9B8BD9";
  for (const [ratio, price] of Object.entries(levels)) {
    // 0.000 and 1.000 are the anchors themselves, already visible as the swing
    // they were taken from. Redrawing them adds two lines and no information.
    if (ratio === "0.000" || ratio === "1.000") continue;
    state.fibLines.push(
      state.chart.addPriceLine({
        price,
        color: colour,
        lineWidth: 1,
        dashed: true,
        id: `fib-${ratio}`,
        label: ratio,
      }),
    );
  }
}

/** Entry and exit markers for every trade in the journal.
 *
 * Coloured by the trade's net, never by its outcome. A target that filled
 * through an adverse gap is still a loss and a stop is not always one, so
 * colouring by outcome would draw the intent rather than the result.
 *
 * Exits carry the R multiple as their label because R is what the position was
 * sized in; the currency figure is in the journal row's tooltip, where reading
 * it is a deliberate act.
 */
export function tradeMarkers(trades: Trade[]): SeriesMarker[] {
  const pos = token("--pos") || "#2FBF84";
  const neg = token("--neg") || "#E8524F";
  const out: SeriesMarker[] = [];

  for (const trade of trades) {
    const won = trade.net > 0;
    const long = trade.side === "LONG";
    out.push({
      time: Math.floor(Date.parse(trade.entry_index) / 1000),
      position: long ? "belowBar" : "aboveBar",
      shape: long ? "arrowUp" : "arrowDown",
      size: "tiny",
      color: token("--ink-3") || "#888",
    });
    out.push({
      time: Math.floor(Date.parse(trade.exit_index) / 1000),
      position: long ? "aboveBar" : "belowBar",
      shape: "circle",
      size: "small",
      color: won ? pos : neg,
      text: `${trade.r_multiple >= 0 ? "+" : ""}${trade.r_multiple.toFixed(1)}R`,
    });
  }
  return out;
}

export async function boot(
  container: HTMLElement,
  panel: HTMLElement,
  header: HTMLElement,
): Promise<void> {
  let symbol = "BTC/USDT";
  let timeframe = "4h";
  let limit = DEFAULT_BARS;
  let view: ViewState = { ...DEFAULT_VIEW, layers: { ...DEFAULT_VIEW.layers } };

  let state = mount(container, view.kind);
  const deskRoot = document.getElementById("desk");
  const railRoot = document.getElementById("rail");
  let selectedAccount: string | null = null;
  let markers: SeriesMarker[] = [];
  let bars: Bar[] = [];

  async function refreshDesk(): Promise<void> {
    if (!deskRoot) return;
    const result = await renderDesk(deskRoot, selectedAccount, (name) => {
      selectedAccount = name;
      void refreshDesk();
    });
    selectedAccount = result.account;
    markers = tradeMarkers(result.trades);
    state.markers.setMarkers(view.layers.trades ? markers : []);
  }

  /** Swap the price series without refetching.
   *
   * The series owns the drawn bars, so changing its type means replacing it.
   * Its markers primitive goes with it, which is why the markers are reapplied
   * rather than assumed to survive. Refetching here instead would spend a
   * fourteen-second download to change how the same bars are shaded. */
  function setKind(kind: SeriesKind): void {
    state.candles.remove();
    const replacement = state.chart.addSeries(kind);
    state.candles = replacement;
    state.markers = replacement.createMarkers();
    replacement.setData(bars);
    state.markers.setMarkers(view.layers.trades ? markers : []);
  }

  async function load(): Promise<void> {
    const [{ bars: fetched, venue }, e20, e50, e200, vwap] = await Promise.all([
      api.ohlcv(symbol, timeframe, limit),
      api.metric("ema", symbol, timeframe, { length: 20 }, limit),
      api.metric("ema", symbol, timeframe, { length: 50 }, limit),
      api.metric("ema", symbol, timeframe, { length: 200 }, limit),
      api.metric("session_vwap", symbol, timeframe, {}, limit),
    ]);

    bars = fetched as Bar[];
    state.candles.setData(bars);
    state.markers.setMarkers(view.layers.trades ? markers : []);

    if (view.layers.ema) {
      overlay(state, "ema200", e200, token("--ink-dim") || "#555");
      overlay(state, "ema50", e50, token("--accent") || "#D98A3D");
      overlay(state, "ema20", e20, token("--info") || "#5B9BD5");
    }
    if (view.layers.vwap) {
      overlay(state, "vwap", vwap, token("--annotate") || "#9B8BD9");
    }

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
      // tail=1: the panel renders one reading per metric. Asking for the whole
      // series to show its last value ships twenty thousand points per metric.
      metrics.map((spec) => api.metric(spec.name, symbol, timeframe, {}, limit, 1)),
    );
    panel.replaceChildren(...responses.map(metricRow));

    const zoneResponse = responses.find((r) => r.name === "supply_demand_zones");
    drawZones(state, view.layers.zones ? zoneResponse?.zones ?? [] : []);

    const fibResponse = responses.find((r) => r.name === "fibonacci_levels");
    drawFib(state, view.layers.fib ? fibResponse?.scalars ?? {} : {});
  }

  /** Apply a layer change without refetching.
   *
   * Hiding a layer removes what is drawn and nothing else: the engine still
   * computed it and the panel still reports it. A toggle that skipped the
   * computation would make a hidden level and an absent one look identical,
   * which is the same confusion the degradation contract exists to prevent. */
  function applyLayers(): void {
    state.markers.setMarkers(view.layers.trades ? markers : []);
    if (!view.layers.ema) {
      for (const key of ["ema20", "ema50", "ema200"]) {
        state.overlays.get(key)?.remove();
        state.overlays.delete(key);
      }
    }
    if (!view.layers.vwap) {
      state.overlays.get("vwap")?.remove();
      state.overlays.delete("vwap");
    }
  }

  if (railRoot) {
    const drawRail = () =>
      renderRail(railRoot, view, async (next) => {
        const kindChanged = next.kind !== view.kind;
        const needsRedraw =
          next.layers.ema !== view.layers.ema ||
          next.layers.vwap !== view.layers.vwap ||
          next.layers.zones !== view.layers.zones ||
          next.layers.fib !== view.layers.fib;
        view = next;
        drawRail();
        if (kindChanged) setKind(view.kind);
        applyLayers();
        if (needsRedraw) await load();
      });
    drawRail();
  }

  const controls = document.getElementById("controls");
  if (controls) {
    const timeframes = ["1h", "4h", "1d"];
    const render = () => {
      controls.replaceChildren();
      for (const value of SYMBOLS) {
        controls.append(
          chip(value.replace("/USDT", ""), value === symbol, async () => {
            symbol = value;
            render();
            await load();
          }),
        );
      }
      controls.append(el("span", "csep"));
      for (const value of timeframes) {
        controls.append(chip(value, value === timeframe, async () => {
          timeframe = value;
          render();
          await load();
        }));
      }
      controls.append(el("span", "csep"));
      for (const [label, value] of BAR_CHOICES) {
        controls.append(chip(label, value === limit, async () => {
          limit = value;
          render();
          await load();
        }));
      }
    };
    render();
  }

  await refreshDesk();
  await load();
}

function el(tag: string, className: string): HTMLElement {
  const node = document.createElement(tag);
  node.className = className;
  return node;
}

function chip(label: string, active: boolean, onClick: () => void): HTMLElement {
  const button = document.createElement("button");
  button.className = active ? "chip is-active" : "chip";
  button.type = "button";
  button.textContent = label;
  button.addEventListener("click", onClick);
  return button;
}
