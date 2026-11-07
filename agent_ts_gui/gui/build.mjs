// Bundle main + preload + renderer with esbuild into dist.
import { build } from "esbuild";
import { cpSync, rmSync } from "node:fs";

await build({
  entryPoints: ["src/main/index.ts", "src/preload/index.ts"],
  outdir: "dist",
  outbase: "src",
  bundle: true,
  platform: "node",
  format: "cjs",
  target: "node20",
  external: ["electron"],
});

// renderer: copy static assets, then bundle app.ts over them
rmSync("dist/renderer", { recursive: true, force: true });
cpSync("src/renderer", "dist/renderer", { recursive: true });
await build({
  entryPoints: ["src/renderer/app.ts"],
  outfile: "dist/renderer/app.js",
  bundle: true,
  platform: "browser",
  format: "iife",
  target: "chrome120",
});

console.log("build ok");
