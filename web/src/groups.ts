/** Grouping for the metrics panel.
 *
 * Twenty-one readings in one flat column is a wall nobody scans. Grouped by
 * what a reader is asking, the question picks the section: "where is this
 * going" is Trend, "is the move tiring" is Momentum, "what has structure done"
 * is Structure, "where did business get done" is Profile.
 *
 * The grouping is declared here rather than inferred from a metric's module,
 * because the modules are organised by the engine's analytical bands and those
 * are not the questions a person asks a chart.
 */

export type GroupKey = "trend" | "momentum" | "structure" | "profile";

export const GROUPS: [GroupKey, string, string[]][] = [
  [
    "trend",
    "Trend",
    ["ema", "trend_state", "session_vwap"],
  ],
  [
    "momentum",
    "Momentum",
    ["rsi", "macd", "atr", "true_range"],
  ],
  [
    "structure",
    "Structure",
    [
      "change_of_character",
      "break_of_structure",
      "confirmed_swing_levels",
      "swing_pivots",
      "supply_demand_zones",
      "fibonacci_levels",
      "imbalance",
      "imbalance_bands",
    ],
  ],
  [
    "profile",
    "Profile",
    ["value_area", "volume_profile"],
  ],
];

/** Candles get their own group because they are a different kind of claim: a
 *  pattern is a shape someone named, not a measurement. Keeping them apart
 *  stops a boolean sitting next to a price as though they were comparable. */
export const PATTERNS = [
  "bullish_engulfing",
  "bearish_engulfing",
  "hammer",
  "shooting_star",
];

/** Open by default, except patterns. A reader wants trend and structure on
 *  sight; four booleans can wait to be asked for. */
export const DEFAULT_OPEN: Record<string, boolean> = {
  trend: true,
  momentum: true,
  structure: true,
  profile: false,
  patterns: false,
};

/** Anything the registry grows that this file has not been told about.
 *
 * Returned rather than dropped: a metric silently missing from the panel is a
 * metric nobody notices is missing, and the engine gains them faster than this
 * list is edited. */
export function ungrouped(names: string[]): string[] {
  const known = new Set([...GROUPS.flatMap(([, , list]) => list), ...PATTERNS]);
  return names.filter((name) => !known.has(name));
}
