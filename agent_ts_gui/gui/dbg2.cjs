const { spawn } = require("child_process");
const http = require("http");
const electron = require("electron");
const WebSocket = require("ws");
const proc = spawn(electron, [".", "--no-sandbox", "--disable-gpu", "--remote-debugging-port=9348", "--root", "/tmp/proj_a"], { cwd: __dirname, stdio: ["ignore","pipe","pipe"] });
const wait = (ms) => new Promise((r) => setTimeout(r, ms));
function getWsUrl() { return new Promise((resolve) => { http.get("http://127.0.0.1:9348/json", (res) => { let b=""; res.on("data",(d)=>b+=d); res.on("end",()=>{ try{ const l=JSON.parse(b); const p=l.find(t=>t.type==="page"); resolve(p?p.webSocketDebuggerUrl:null);}catch{resolve(null);} }); }).on("error",()=>resolve(null)); }); }
(async () => {
  let url=null; for (let i=0;i<100;i++){ url=await getWsUrl(); if(url) break; await wait(200); }
  const ws = new WebSocket(url, { perMessageDeflate: false });
  let id=0; const pending=new Map();
  ws.on("message",(buf)=>{ const m=JSON.parse(buf); if(m.id&&pending.has(m.id)){pending.get(m.id)(m);pending.delete(m.id);} });
  const send=(method,params={})=>new Promise((r)=>{ id+=1; pending.set(id,r); ws.send(JSON.stringify({id,method,params})); });
  await new Promise((r)=>ws.on("open",r));
  const evalJs=(expr)=>send("Runtime.evaluate",{expression:expr,returnByValue:true,awaitPromise:true}).then(r=>r.result&&r.result.result?r.result.result.value:undefined);
  for (let i=0;i<120;i++){ const l=await evalJs("document.getElementById('root-label')&&document.getElementById('root-label').textContent"); if(l&&l.includes("proj_a")) break; await wait(500); }
  console.log("busy:", await evalJs("S && S.busy"));
  await evalJs("document.getElementById('projects-btn').click()");
  await wait(300);
  console.log("backdrop show:", await evalJs("document.getElementById('modal-backdrop').className"));
  console.log("modal html:", await evalJs("document.getElementById('modal').innerHTML.slice(0,200)"));
  ws.close(); proc.kill("SIGKILL"); process.exit(0);
})().catch((e)=>{ console.error(e); proc.kill("SIGKILL"); process.exit(1); });
