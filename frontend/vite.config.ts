import { defineConfig } from "vite";
const target = process.env.FLOWFORGE_API_PROXY ?? "http://api:8000";
export default defineConfig({ server: { proxy: { "/health": target, "/api": target } } });
