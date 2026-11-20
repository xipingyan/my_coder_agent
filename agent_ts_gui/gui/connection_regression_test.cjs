const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const vm = require("node:vm");
const { test } = require("node:test");
const ts = require("typescript");

function compile(source) {
  return ts.transpileModule(source, {
    compilerOptions: { module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2022 },
  }).outputText;
}

test("backend readiness accepts POSIX LF and Windows CRLF stdout", () => {
  const source = fs.readFileSync(path.join(__dirname, "src/main/backend-output.ts"), "utf8");
  const context = { exports: {} };
  vm.runInNewContext(compile(source), context);
  const parse = context.exports.backendUrlFromLine;
  for (const eol of ["\n", "\r\n"]) {
    const lines = `log${eol}WS_URL=ws://127.0.0.1:8931${eol}`.split("\n");
    assert.equal(parse(lines[0]), null);
    assert.equal(parse(lines[1]), "ws://127.0.0.1:8931");
  }
  assert.equal(parse("WS_URL=not a URL"), null);
});

// Exercise the actual renderer RPC section without needing an Electron window.
function rpcClient(send, getBackendUrl = async () => "ws://127.0.0.1:8931") {
  const source = fs.readFileSync(path.join(__dirname, "src/renderer/app.ts"), "utf8");
  const start = source.indexOf("let nextId = 0;");
  const end = source.indexOf("// ------------------------------------------------------------------- status", start);
  assert.ok(start >= 0 && end > start);
  const timers = [];
  let reply;
  const context = {
    window: { api: { send, getBackendUrl, onReply: cb => { reply = cb; } } },
    setTimeout: (cb, delay) => { timers.push({ cb, delay }); },
  };
  vm.createContext(context);
  vm.runInContext(compile(source.slice(start, end)), context);
  return { call: context.call, reply: msg => reply(msg), timers };
}

test("RPCs waiting for readiness retain distinct IDs", async () => {
  const sent = [];
  const client = rpcClient(payload => { sent.push(payload); return Promise.resolve(); });
  const first = client.call("context_strategies", {}, { waitReady: true });
  const second = client.call("default_config", {}, { waitReady: true });
  await new Promise(resolve => setImmediate(resolve));
  assert.deepEqual(sent.map(p => p.id), [1, 2]);
  client.reply({ id: 2, result: "defaults" });
  client.reply({ id: 1, result: "strategies" });
  assert.equal(await first, "strategies");
  assert.equal(await second, "defaults");
});

test("failed RPC does not delete a concurrent request", async () => {
  const client = rpcClient(p => p.id === 1
    ? Promise.reject(new Error("offline")) : Promise.resolve());
  const first = client.call("context_strategies");
  const second = client.call("default_config");
  const result = await first;
  assert.match(result.__error, /offline/);
  client.reply({ id: 2, result: "defaults" });
  assert.equal(await second, "defaults");
});

test("RPC timeout cleans up only its own request", async () => {
  const client = rpcClient(() => Promise.resolve());
  const first = client.call("context_strategies");
  const second = client.call("default_config");
  client.timers[0].cb();
  assert.equal((await first).__error, "request timed out");
  client.reply({ id: 2, result: "defaults" });
  assert.equal(await second, "defaults");
});