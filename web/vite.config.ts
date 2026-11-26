import { defineConfig } from "vite";

export default defineConfig({
  server: {
    port: 5173,
    strictPort: true,
    // design/tokens.css lives above this root. Without this Vite refuses to
    // serve it and every var() silently resolves to nothing, which renders as
    // an unstyled page with no error in the console.
    fs: { allow: [".."] },
    // Without this the browser would make a cross-origin request to the engine
    // and be blocked, which would mean adding CORS to a loopback-only API.
    proxy: {
      "/api": {
        target: "http://127.0.0.1:8787",
        changeOrigin: false,
        rewrite: (path) => path.replace(/^\/api/, ""),
      },
    },
  },
});
