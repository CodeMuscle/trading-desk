/** The left rail: what the chart draws, and how it draws it.
 *
 * Every control here is a view toggle. Nothing in this file fetches, computes
 * or trades; it decides which of the things the engine already returned are
 * visible. That separation is deliberate, because a layer control that quietly
 * changes what is computed turns "hide the zones" into "the zones are gone",
 * and a reader cannot tell a hidden level from an absent one.
 *
 * Candlesticks are the default and stay first in the list. The alternatives are
 * there because some readings are easier in another form: a line hides the
 * intrabar range, which is exactly what you want when comparing a long sweep,
 * and exactly what you must not use when judging a wick.
 */

export type SeriesKind =
  | "candlestick"
  | "hollow-candle"
  | "bar"
  | "line"
  | "area"
  | "baseline";

export const SERIES_KINDS: [SeriesKind, string, string][] = [
  ["candlestick", "Candles", "Open, high, low and close. The default, and the only form that shows a wick."],
  ["hollow-candle", "Hollow", "Candles with the body hollow when the close is above the open."],
  ["bar", "Bars", "OHLC bars. Thinner than candles at a wide zoom."],
  ["line", "Line", "Closes only. Hides the intrabar range, so never judge a wick on it."],
  ["area", "Area", "Closes with a fill. The same information as the line."],
  ["baseline", "Baseline", "Closes, coloured above and below a reference level."],
];

export type LayerKey = "ema" | "vwap" | "zones" | "trades" | "fib";

export const LAYERS: [LayerKey, string, string][] = [
  ["ema", "EMA", "20, 50 and 200 period exponential moving averages."],
  ["vwap", "VWAP", "Session volume-weighted average price."],
  ["zones", "Zones", "Supply and demand bands. Solid is fresh, dashed has been tested."],
  ["fib", "Fib", "Retracements between the last confirmed swing high and low."],
  ["trades", "Trades", "Entries and exits from the selected paper account."],
];

export type ViewState = {
  kind: SeriesKind;
  layers: Record<LayerKey, boolean>;
};

export const DEFAULT_VIEW: ViewState = {
  kind: "candlestick",
  layers: { ema: true, vwap: true, zones: true, trades: true, fib: false },
};

function button(
  label: string,
  title: string,
  active: boolean,
  onClick: () => void,
): HTMLElement {
  const node = document.createElement("button");
  node.type = "button";
  node.className = active ? "rail-btn is-active" : "rail-btn";
  node.textContent = label;
  node.title = title;
  node.setAttribute("aria-pressed", String(active));
  node.addEventListener("click", onClick);
  return node;
}

function group(title: string): HTMLElement {
  const node = document.createElement("div");
  node.className = "rail-group";
  const heading = document.createElement("div");
  heading.className = "rail-title";
  heading.textContent = title;
  node.append(heading);
  return node;
}

export function renderRail(
  root: HTMLElement,
  view: ViewState,
  onChange: (next: ViewState) => void,
): void {
  root.replaceChildren();

  const kinds = group("Type");
  for (const [kind, label, title] of SERIES_KINDS) {
    kinds.append(
      button(label, title, view.kind === kind, () =>
        onChange({ ...view, kind }),
      ),
    );
  }

  const layers = group("Layers");
  for (const [key, label, title] of LAYERS) {
    layers.append(
      button(label, title, view.layers[key], () =>
        onChange({ ...view, layers: { ...view.layers, [key]: !view.layers[key] } }),
      ),
    );
  }

  root.append(kinds, layers);
}
