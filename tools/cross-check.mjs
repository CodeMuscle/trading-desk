import { readFileSync } from "fs";
import { rsiSeries, emaSeries } from "openalgo-charts";

const bars = JSON.parse(readFileSync("/tmp/bars.json", "utf8"));
const py = JSON.parse(readFileSync("/tmp/py.json", "utf8"));

const num = (x) => (typeof x === "number" ? x : x && typeof x === "object"
  ? (x.value ?? x.close ?? x.y ?? NaN) : NaN);

function compare(label, mine, theirs, tol) {
  let compared = 0, worst = 0, at = -1;
  const n = Math.min(mine.length, theirs.length);
  for (let i = 0; i < n; i++) {
    const a = Number(mine[i]), b = num(theirs[i]);
    if (!Number.isFinite(a) || !Number.isFinite(b)) continue;
    compared++;
    const d = Math.abs(a - b);
    if (d > worst) { worst = d; at = i; }
  }
  if (compared < n * 0.5) {
    console.log(`  ${label.padEnd(9)} INCONCLUSIVE: only ${compared}/${n} comparable`);
    return;
  }
  const rel = worst / Math.abs(Number(mine[n - 1]) || 1);
  console.log(`  ${label.padEnd(9)} compared ${compared}/${n} | max abs ${worst.toExponential(2)} | rel ${rel.toExponential(2)} | ${worst <= tol ? "AGREE" : "DISAGREE at bar " + at}`);
}

const theirRsi = rsiSeries(bars, 14);
const theirEma = emaSeries(bars, 20);
console.log("their rsi[20]:", JSON.stringify(theirRsi[20]), " their ema[20]:", JSON.stringify(theirEma[20]));
console.log("ours  rsi[20]:", py.rsi14[20], "        ours  ema[20]:", py.ema20[20]);
console.log("");
compare("RSI(14)", py.rsi14, theirRsi, 1e-6);
compare("EMA(20)", py.ema20, theirEma, 1e-6);
