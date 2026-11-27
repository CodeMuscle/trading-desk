/** The paper desk panel: account state, journal and refusals.
 *
 * Read-only by construction. The engine exposes no write route at all, so there
 * is nothing here that could place an order even by accident; advancing an
 * account is a CLI action taken by a person or their scheduler.
 *
 * Trades are shown in R rather than only in currency. R is the unit the risk
 * engine sizes in, so it is the unit in which a loss is either the planned one
 * or a stop that did not hold, and currency alone cannot tell those apart.
 */

import { api, type Account, type Refusal, type Trade } from "./api";

/** `cmd` keeps its line breaks because a shell command must not be rewrapped;
 *  ordinary prose is left to the column width. */
function el(tag: string, className?: string, text?: string): HTMLElement {
  const node = document.createElement(tag);
  if (className) node.className = className;
  if (text !== undefined) node.textContent = text;
  return node;
}

function money(value: number): string {
  return value.toLocaleString("en-US", { maximumFractionDigits: 0 });
}

function signed(value: number, digits = 2): string {
  return `${value >= 0 ? "+" : ""}${value.toLocaleString("en-US", {
    maximumFractionDigits: digits,
  })}`;
}

/** A trade's class is its sign, never its outcome.
 *
 * A target that filled through an adverse gap can still be a loss, and a stop
 * is not always one. Colouring by outcome would state the intent instead of the
 * result. */
function toneFor(net: number): string {
  return net > 0 ? "pos" : net < 0 ? "neg" : "flat";
}

export function accountRow(account: Account, active: boolean): HTMLElement {
  const row = el("button", active ? "acct is-active" : "acct");
  (row as HTMLButtonElement).type = "button";
  row.append(
    el("span", "acct-name", account.name),
    el("span", `acct-ret ${toneFor(account.return_pct)}`, `${signed(account.return_pct)}%`),
    el("span", "acct-eq", money(account.equity)),
  );
  if (account.position_open) {
    const open = el("span", "acct-open", "OPEN");
    open.title = "A position is live on this account.";
    row.append(open);
  }
  return row;
}

export function tradeRow(trade: Trade): HTMLElement {
  const row = el("div", `trow ${toneFor(trade.net)}`);
  const when = trade.exit_index.slice(0, 16).replace("T", " ");
  row.append(
    el("span", "t-when", when),
    el("span", `t-side ${trade.side.toLowerCase()}`, trade.side),
    el("span", "t-out", trade.outcome === "end_of_data" ? "open-end" : trade.outcome),
    el("span", "t-r", `${signed(trade.r_multiple)}R`),
    el("span", "t-net", signed(trade.net, 0)),
  );
  row.title =
    `entry ${trade.entry.toFixed(2)} → exit ${trade.exit.toFixed(2)}\n` +
    `stop ${trade.stop.toFixed(2)}  target ${trade.target.toFixed(2)}\n` +
    `MAE ${trade.mae.toFixed(2)}R  MFE ${trade.mfe.toFixed(2)}R` +
    (trade.lag_seconds === null
      ? "\nlag not observed (backfilled bar)"
      : `\npoller lag ${Math.round(trade.lag_seconds)}s`);
  return row;
}

export function refusalRow(refusal: Refusal): HTMLElement {
  const row = el("div", "rrow");
  row.append(
    el("span", "r-when", refusal.bar_index.slice(0, 16).replace("T", " ")),
    el("span", "r-why", refusal.reason),
  );
  row.title = refusal.reason;
  return row;
}

export type DeskPanel = {
  /** Trades for the selected account, so the chart can mark them. */
  trades: Trade[];
  account: string | null;
};

export async function renderDesk(
  root: HTMLElement,
  selected: string | null,
  onSelect: (name: string) => void,
): Promise<DeskPanel> {
  const { accounts } = await api.accounts();
  root.replaceChildren();

  if (accounts.length === 0) {
    const empty = el(
      "div",
      "empty cmd",
      "No paper accounts yet.\nCreate one:  uv run desk create swing --equity 100000",
    );
    root.append(empty);
    return { trades: [], account: null };
  }

  const active = selected && accounts.some((a) => a.name === selected)
    ? selected
    : accounts[0].name;

  const list = el("div", "acctlist");
  for (const account of accounts) {
    const row = accountRow(account, account.name === active);
    row.addEventListener("click", () => onSelect(account.name));
    list.append(row);
  }
  root.append(el("div", "ptitle", "Accounts"), list);

  const [journal, { refusals }] = await Promise.all([
    api.journal(active),
    api.refusals(active),
  ]);

  const summary = el("div", "summary");
  const { count, net, win_rate, expectancy_r } = journal.summary;
  if (count === 0) {
    summary.append(
      el(
        "div",
        "empty",
        "No trades yet. This account holds forward results only, so it stays " +
          "empty until a bar closes on a signal.",
      ),
    );
  } else {
    const stat = (label: string, value: string, tone = "") => {
      const box = el("div", "stat");
      box.append(el("span", "s-label", label), el("span", `s-value ${tone}`, value));
      return box;
    };
    summary.append(
      stat("trades", String(count)),
      stat("net", signed(net, 0), toneFor(net)),
      // Rendered only when it exists. A win rate of 0% on no trades is a claim.
      stat("win", win_rate === null ? "–" : `${(win_rate * 100).toFixed(1)}%`),
      stat(
        "expectancy",
        expectancy_r === null ? "–" : `${signed(expectancy_r)}R`,
        expectancy_r === null ? "" : toneFor(expectancy_r),
      ),
    );
  }
  root.append(summary);

  if (count > 0) {
    const trades = el("div", "tlist");
    for (const trade of [...journal.trades].reverse()) trades.append(tradeRow(trade));
    root.append(el("div", "ptitle", `Journal · ${active}`), trades);
  }

  // Always rendered, even at zero. A desk that shows only fills cannot
  // distinguish a strategy that performed badly from one that barely traded.
  const refusalBox = el("div", "rlist");
  if (refusals.length === 0) {
    refusalBox.append(el("div", "empty", "No refusals. Every signal was sized and taken."));
  } else {
    for (const refusal of [...refusals].reverse().slice(0, 50)) {
      refusalBox.append(refusalRow(refusal));
    }
  }
  root.append(
    el("div", "ptitle", `Refused · ${refusals.length}`),
    refusalBox,
  );

  return { trades: journal.trades, account: active };
}
