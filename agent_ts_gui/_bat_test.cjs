// Run start_gui.bat and capture output; kill ONLY this spawned pid tree.
const { spawn, execSync } = require("node:child_process");
const p = spawn("cmd.exe", ["/c", "start_gui.bat"], {
  cwd: __dirname, stdio: ["ignore", "pipe", "pipe"],
});
let out = "";
p.stdout.on("data", d => out += d);
p.stderr.on("data", d => out += d);
setTimeout(() => {
  console.log(out.slice(-3000));
  try { execSync(`taskkill /F /T /PID ${p.pid}`, { stdio: "ignore" }); } catch {}
  process.exit(0);
}, 30000);
