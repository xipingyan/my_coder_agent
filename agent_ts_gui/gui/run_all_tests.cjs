// Run every CDP smoke test in sequence against the dev build.
// The remote test needs a standalone server on 127.0.0.1:8931; this runner
// starts one (via the backend venv) just before that test and kills it after.
//
//   node run_all_tests.cjs             # run everything
//   node run_all_tests.cjs proj boot   # run only the named tests
const { spawn, spawnSync, execSync } = require("node:child_process");
const fs = require("node:fs");
const os = require("node:os");
const path = require("node:path");

const GUI = __dirname;
const BACKEND = path.join(__dirname, "..", "backend");
const REMOTE_PORT = 8931;

// name -> script. Order matters only for readability.
const SUITES = {
  boot: "cdp_boot_test.cjs",
  proj: "cdp_proj_test.cjs",
  proj_switch: "cdp_proj_switch_test.cjs",
  localpick: "cdp_localpick_test.cjs",
  local_add: "cdp_local_add_test.cjs",
  ctxmenu: "cdp_ctxmenu_test.cjs",
  split: "cdp_split_test.cjs",
  files: "cdp_files_test.cjs",
  theme: "cdp_theme_test.cjs",
  remote: "cdp_remote_test.cjs",
};

function pick() {
  const args = process.argv.slice(2);
  if (!args.length) return Object.keys(SUITES);
  const names = [];
  for (const a of args) {
    if (!SUITES[a]) { console.error(`unknown test: ${a} (choose from ${Object.keys(SUITES).join(", ")})`); process.exit(2); }
    if (!names.includes(a)) names.push(a);
  }
  return names;
}

function portListening(port) {
  try { return execSync(`ss -ltn 2>/dev/null | grep -q :${port}$ && echo up || echo down`, { shell: "/bin/bash" }).toString().trim() === "up"; }
  catch { return false; }
}

// start the remote server detached so it survives this runner's shell
function startRemoteServer() {
  const venv = path.join(BACKEND, ".venv", "bin", "python");
  const log = path.join(os.tmpdir(), "remote_srv.log");
  const out = fs.openSync(log, "a");
  const child = spawn(venv, ["-m", "coder_agent.server", "--host", "127.0.0.1", "--port", String(REMOTE_PORT)],
    { cwd: BACKEND, detached: true, stdio: ["ignore", out, out] });
  child.unref();
  for (let i = 0; i < 30; i++) {
    if (portListening(REMOTE_PORT)) return;
    execSync("sleep 0.5");
  }
  throw new Error(`remote server did not start on :${REMOTE_PORT} (see ${log})`);
}

function stopRemoteServer() {
  try {
    execSync(`for p in $(ps -eo pid,cmd | awk '/coder_agent.server --host 127.0.0.1 --port ${REMOTE_PORT}/ && !/awk/ {print $1}'); do kill $p 2>/dev/null; done`,
      { shell: "/bin/bash", stdio: "ignore" });
  } catch { /* ignore */ }
}

function runOne(name) {
  const script = path.join(GUI, SUITES[name]);
  const t0 = Date.now();
  console.log(`\n=== ${name} (${SUITES[name]}) ===`);
  const r = spawnSync(process.execPath, [script], { stdio: "inherit" });
  const secs = ((Date.now() - t0) / 1000).toFixed(1);
  return { name, ok: r.status === 0, secs };
}

(async () => {
  const names = pick();
  const results = [];
  let remoteOwned = false; // true if WE started the server (so we must clean it up)

  // Always build first: the tests run `electron .` against dist/, which is a
  // build artifact. A stale dist would make the suite test old code.
  console.log("[runner] building (esbuild) ...");
  const b = spawnSync("node", ["build.mjs"], { cwd: GUI, stdio: "inherit" });
  if (b.status !== 0) { console.error("[runner] build failed"); process.exit(1); }

  for (const name of names) {
    if (name === "remote" && !portListening(REMOTE_PORT)) {
      console.log(`\n[runner] starting remote server on :${REMOTE_PORT}`);
      startRemoteServer();
      remoteOwned = true;
    }
    results.push(runOne(name));
  }
  if (remoteOwned) stopRemoteServer();

  console.log("\n================ SUMMARY ================");
  let allOk = true;
  for (const r of results) {
    console.log(`${r.ok ? "PASS" : "FAIL"}  ${r.name.padEnd(14)} ${r.secs}s`);
    if (!r.ok) allOk = false;
  }
  console.log(`\n${allOk ? "ALL PASSED" : "SOME FAILED"} (${results.filter(r => r.ok).length}/${results.length})`);
  process.exit(allOk ? 0 : 1);
})();
