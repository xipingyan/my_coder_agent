// Bundle main + preload with esbuild, then copy the renderer assets into dist.
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

// renderer is plain HTML/CSS/JS: copy it verbatim
rmSync("dist/renderer", { recursive: true, force: true });
cpSync("src/renderer", "dist/renderer", { recursive: true });
console.log("build ok");
