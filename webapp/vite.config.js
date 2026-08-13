import { resolve } from "node:path";
import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

// Two entry points on purpose. The landing page must open instantly, so it
// carries no React and no MapLibre; the map application, which loads several
// megabytes of GeoJSON, lives behind its own document.
export default defineConfig({
  plugins: [react()],
  server: { port: 5175, host: true },
  build: {
    rollupOptions: {
      input: {
        landing: resolve(__dirname, "index.html"),
        en: resolve(__dirname, "en.html"),
        mapa: resolve(__dirname, "mapa.html"),
      },
    },
  },
});
