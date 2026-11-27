/** Volume bars drawn as rounded pills.
 *
 * The library's histogram series has exactly two style fields, colour and
 * base, so the rounding cannot come from it. This is a canvas primitive
 * instead: it reads the same bars, converts them with the pane's own time and
 * price scales, and paints a rounded rect per bar.
 *
 * The histogram series stays, drawn transparent. It is what creates the second
 * pane, owns its price scale and autoscales it, and reproducing that here
 * would mean a second scale that disagrees with the one the axis labels.
 *
 * Honest about the limit: at the default zoom a bar is four or five pixels
 * wide, and a radius there is smaller than a pixel. The radius is capped at
 * half the bar width so it rounds when you zoom in and degrades to a square
 * bar when bars are dense, rather than turning every bar into a blob.
 */

import type { Bar } from "./api";

type RenderContext = {
  timeScale: { indexToX(index: number): number; visibleRange(): { from: number; to: number } };
  priceScale: { priceToY(price: number): number };
  plotHeight: number;
};

export class VolumePills {
  private bars: Bar[] = [];
  private up = "rgba(47,191,132,0.55)";
  private down = "rgba(232,82,79,0.55)";

  setData(bars: Bar[], up: string, down: string): void {
    this.bars = bars;
    this.up = up;
    this.down = down;
  }

  zOrder(): "bottom" | "normal" | "top" {
    return "normal";
  }

  // Deliberately no autoscaleInfo. The transparent histogram already scales
  // the pane, and it scales to the *visible* bars. An override here saw every
  // bar in the series, so one historic spike of 300,000 set the range and the
  // visible bars collapsed into a line along the axis.

  draw(ctx: CanvasRenderingContext2D, rc: RenderContext): void {
    if (this.bars.length === 0) return;
    const { from, to } = rc.timeScale.visibleRange();
    const first = Math.max(0, Math.floor(from));
    const last = Math.min(this.bars.length - 1, Math.ceil(to));
    if (last < first) return;

    // Bar width from the gap between two neighbours, so it tracks zoom without
    // reading a private barSpacing. The 0.72 leaves the gap that makes a row
    // of bars read as separate marks rather than a filled block.
    const step =
      last > first
        ? Math.abs(rc.timeScale.indexToX(first + 1) - rc.timeScale.indexToX(first))
        : 6;
    const width = Math.max(1, step * 0.72);
    const baseY = rc.priceScale.priceToY(0);

    ctx.save();
    for (let i = first; i <= last; i += 1) {
      const bar = this.bars[i];
      if (!bar) continue;
      const x = rc.timeScale.indexToX(i) - width / 2;
      const y = rc.priceScale.priceToY(bar.volume);
      const height = Math.max(1, baseY - y);
      // Capped at half the width, so a dense chart degrades to square bars
      // rather than to lozenges, and at half the height so a one-pixel bar
      // does not become a circle.
      const radius = Math.min(width / 2, height / 2, 3);

      ctx.fillStyle = bar.close >= bar.open ? this.up : this.down;
      ctx.beginPath();
      if (typeof ctx.roundRect === "function") {
        ctx.roundRect(x, y, width, height, radius);
      } else {
        // Safari before 16 has no roundRect. A square bar is the right
        // fallback: the alternative is no volume at all.
        ctx.rect(x, y, width, height);
      }
      ctx.fill();
    }
    ctx.restore();
  }
}
