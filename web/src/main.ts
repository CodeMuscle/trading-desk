import "../../design/tokens.css";

import { boot } from "./chart";

const container = document.getElementById("chart");
const panel = document.getElementById("panel");
const header = document.getElementById("header");

if (container && panel && header) {
  boot(container, panel, header).catch((error: unknown) => {
    panel.textContent = "";
    const message = document.createElement("div");
    message.className = "boot-error";
    message.textContent =
      "Engine unreachable. Start it with:  uv run python -m desk.api.app\n\n" +
      String(error);
    panel.append(message);
  });
}
