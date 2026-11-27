/** Dark, light, or follow the system.
 *
 * Three states, not two. "System" is a real choice and the default: a desk open
 * all day should dim with the machine at night rather than stay bright because
 * someone once clicked dark. It is stored as the absence of a stamp, so the
 * tokens' own `prefers-color-scheme` media query decides.
 *
 * `design/tokens.css` is dark-first and switches on `data-theme`, so this
 * writes nothing but that attribute. Putting colour decisions here instead
 * would split the palette across two files, and the one in CSS would quietly
 * stop being the source of truth.
 */

export type Theme = "system" | "dark" | "light";

const KEY = "desk.theme";

export function readTheme(): Theme {
  const stored = localStorage.getItem(KEY);
  return stored === "dark" || stored === "light" ? stored : "system";
}

export function applyTheme(theme: Theme): void {
  const root = document.documentElement;
  if (theme === "system") {
    // No stamp: prefers-color-scheme decides, which is what "system" means.
    delete root.dataset.theme;
    localStorage.removeItem(KEY);
  } else {
    root.dataset.theme = theme;
    localStorage.setItem(KEY, theme);
  }
}

/** True when the page is currently rendering dark, whichever route got it
 *  there. The chart needs the resolved answer, not the preference. */
export function isDarkNow(): boolean {
  const stamped = document.documentElement.dataset.theme;
  if (stamped === "dark") return true;
  if (stamped === "light") return false;
  return window.matchMedia("(prefers-color-scheme: dark)").matches;
}

export function mountThemeToggle(
  root: HTMLElement,
  onChange: (theme: Theme) => void,
): void {
  const options: [Theme, string, string][] = [
    ["system", "Auto", "Follow the operating system's appearance."],
    ["dark", "Dark", "Always dark."],
    ["light", "Light", "Always light."],
  ];
  const buttons = new Map<Theme, HTMLElement>();
  let current = readTheme();

  const paint = () => {
    for (const [value, node] of buttons) {
      node.classList.toggle("is-active", value === current);
      node.setAttribute("aria-pressed", String(value === current));
    }
  };

  for (const [value, label, title] of options) {
    const node = document.createElement("button");
    node.type = "button";
    node.className = "chip";
    node.textContent = label;
    node.title = title;
    node.addEventListener("click", () => {
      current = value;
      applyTheme(value);
      paint();
      onChange(value);
    });
    buttons.set(value, node);
    root.append(node);
  }
  paint();

  // The system can change under a page that is already open, and "Auto" has to
  // mean it. Without this the desk stays light until it is reloaded.
  window
    .matchMedia("(prefers-color-scheme: dark)")
    .addEventListener("change", () => {
      if (readTheme() === "system") onChange("system");
    });
}
