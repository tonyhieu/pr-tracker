import react from "@vitejs/plugin-react";
import { defineConfig } from "vite";

export default defineConfig({
  plugins: [react()],
  build: { outDir: "../prt/static", emptyOutDir: true },
  server: {
    proxy: { "/api": process.env.PRT_URL ?? "http://127.0.0.1:4747" },
  },
});
