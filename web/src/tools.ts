/** The drawing toolbar, wired to the chart library's own drawing tier.
 *
 * None of this geometry is hand-built. openalgo-charts ships the tools, their
 * hit-testing, their handles, their settings schema and their icons, and
 * re-implementing any of it would be a second, worse set that drifts from the
 * one the chart already hit-tests against.
 *
 * Persistence is per symbol *and* timeframe, scoped inside an account. The
 * library's own key is symbol and exchange only, which would carry a trendline
 * drawn on the 4h across to the daily, where the bars it was anchored to do not
 * exist. Timeframe goes in the key for that reason, and the account prefix
 * keeps two accounts from sharing a workspace.
 */

import {
  DrawingController,
  BRUSH,
  CALLOUT,
  ELLIPSE,
  FIB_RETRACEMENT,
  HORIZONTAL_LINE,
  LONG_POSITION,
  MEASURE,
  RAY,
  RECTANGLE,
  SHORT_POSITION,
  TEXT,
  TREND_LINE,
  VERTICAL_LINE,
  chromeIconSvg,
  createInstrumentDrawings,
  iconSvg,
  registerBuiltinDrawingTools,
  webStorageDrawingStore,
} from "openalgo-charts/draw";

type Tool = { id: string; name: string };

/** An icon, or a readable stand-in if the library has no glyph for it.
 *
 * iconSvg throws on an unknown id, and a toolbar built by listing ids is
 * exactly where a wrong one appears. One missing glyph should cost one button,
 * not the whole page: asking for a tool icon by a chrome icon's name took the
 * desk down to a blank screen with the chart never mounting.
 */
function glyph(make: () => string, label: string): string {
  try {
    return make();
  } catch {
    const initials = label.slice(0, 2).toUpperCase();
    return `<span class="tool-fallback">${initials}</span>`;
  }
}

/** The subset a swing desk reaches for, in the order TradingView groups them:
 *  lines, then shapes, then measurement, then annotation. The library
 *  registers many more; a rail listing all of them is a rail nobody scans. */
const TOOLS: (Tool | "gap")[] = [
  TREND_LINE,
  RAY,
  HORIZONTAL_LINE,
  VERTICAL_LINE,
  "gap",
  RECTANGLE,
  ELLIPSE,
  BRUSH,
  "gap",
  FIB_RETRACEMENT,
  MEASURE,
  LONG_POSITION,
  SHORT_POSITION,
  "gap",
  TEXT,
  CALLOUT,
];

export type DrawingSurface = {
  controller: DrawingController;
  /** Point the store at a different symbol/timeframe, loading its drawings. */
  focus: (symbol: string, timeframe: string) => void;
  clear: () => void;
};

export function mountTools(
  root: HTMLElement,
  chart: unknown,
  account: () => string,
): DrawingSurface {
  registerBuiltinDrawingTools();

  // eslint-disable-next-line @typescript-eslint/no-explicit-any
  const controller = new DrawingController(chart as any);

  let timeframe = "4h";
  const instruments = createInstrumentDrawings(
    // eslint-disable-next-line @typescript-eslint/no-explicit-any
    chart as any,
    controller,
    {
      // The trailing colon is part of the prefix: the library concatenates it
      // with the key, so without one the stored name reads
      // "desk.drawingsreplay-4h:BTC/USDT:4h".
      store: webStorageDrawingStore(window.localStorage, "desk.drawings:"),
      // Account, symbol and timeframe. A trendline anchored to 4h bars has no
      // meaning on the daily chart, where those bars do not exist.
      key: (instrument) =>
        instrument?.symbol
          ? `${account()}:${instrument.symbol}:${timeframe}`
          : null,
    },
  );

  let active: string | null = null;
  const buttons = new Map<string | null, HTMLElement>();

  const paint = () => {
    for (const [id, node] of buttons) {
      node.classList.toggle("is-active", id === active);
      node.setAttribute("aria-pressed", String(id === active));
    }
  };

  const arm = (id: string | null) => {
    // Clicking the armed tool disarms it, which is how every drawing program
    // behaves and avoids a rail with no way back to the cursor.
    active = active === id ? null : id;
    controller.setTool(active);
    paint();
  };

  const button = (id: string | null, name: string, glyph: string) => {
    const node = document.createElement("button");
    node.type = "button";
    node.className = "tool-btn";
    node.title = name;
    node.setAttribute("aria-label", name);
    node.innerHTML = glyph;
    node.addEventListener("click", () => arm(id));
    buttons.set(id, node);
    root.append(node);
    return node;
  };

  // Chrome glyphs and tool glyphs are separate registries. "cursor" and
  // "trash" live in the chrome set, and asking iconSvg for them throws, which
  // is how the whole page failed to boot over one wrong icon id.
  button(null, "Cursor", glyph(() => chromeIconSvg("cursor"), "cursor"));
  for (const tool of TOOLS) {
    if (tool === "gap") {
      const gap = document.createElement("span");
      gap.className = "tool-gap";
      root.append(gap);
      continue;
    }
    button(tool.id, tool.name, glyph(() => iconSvg(tool.id), tool.name));
  }

  const gap = document.createElement("span");
  gap.className = "tool-gap";
  root.append(gap);

  const wipe = document.createElement("button");
  wipe.type = "button";
  wipe.className = "tool-btn is-danger";
  wipe.title =
    "Delete the selected drawing, or every drawing on this symbol and " +
    "timeframe when nothing is selected. Delete and Backspace do the same.";
  wipe.setAttribute("aria-label", "Clear drawings");
  wipe.innerHTML = glyph(() => chromeIconSvg("trash"), "clear");
  wipe.addEventListener("click", () => {
    // Deletes the selection when there is one, and only wipes everything when
    // there is not. A button that always wipes is the one that loses an
    // afternoon of levels because something was selected and the obvious thing
    // was expected.
    const ids = controller.selection();
    if (ids.length > 0) {
      controller.removeMany([...ids]);
      return;
    }
    // Deliberately not behind a confirm(): a browser dialog blocks the page,
    // and drawings are reproducible by redrawing rather than data the desk
    // cannot recover. The tooltip states the scope.
    //
    // clear() rather than looping remove(): a single call cannot leave the
    // chart half-wiped if an id has already gone.
    controller.clear();
  });
  root.append(wipe);

  // Delete and Backspace remove the selected drawing, which is what every
  // drawing program does and the only way to remove one without wiping the
  // lot. Ignored while a text field has focus, so typing into a callout does
  // not delete the callout.
  const onKey = (event: KeyboardEvent) => {
    if (event.key !== "Delete" && event.key !== "Backspace") return;
    const target = event.target as HTMLElement | null;
    if (
      target &&
      (target.isContentEditable ||
        ["INPUT", "TEXTAREA", "SELECT"].includes(target.tagName))
    ) {
      return;
    }
    const selected = controller.selected?.();
    const ids = Array.isArray(selected) ? selected : selected ? [selected] : [];
    if (ids.length === 0) return;
    event.preventDefault();
    controller.removeMany(ids.map((d) => (typeof d === "string" ? d : d.id)));
  };
  window.addEventListener("keydown", onKey);

  paint();

  return {
    controller,
    focus: (symbol: string, nextTimeframe: string) => {
      timeframe = nextTimeframe;
      instruments.setInstrument({ symbol });
    },
    clear: () => arm(null),
  };
}
