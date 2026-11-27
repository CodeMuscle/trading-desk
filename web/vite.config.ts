import { defineConfig } from "vite";

export default defineConfig({
  // `vite preview` inherits server.proxy, so the local preview of the demo
  // build was proxying /api to the running engine and serving live account
  // and journal JSON. That makes it a poor stand-in for the deploy, where no
  // engine exists: the deploy verifier reported six exposed endpoints against
  // a build that serves none of them. An empty proxy here makes preview behave
  // like the host it is previewing.
  preview: { proxy: {} },
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
