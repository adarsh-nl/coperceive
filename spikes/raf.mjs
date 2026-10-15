const target = process.argv[2];
const debugPort = Number(process.argv[3] || 9222);
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
async function list() {
  const res = await fetch(`http://127.0.0.1:${debugPort}/json`);
  return res.json();
}
let page;
for (let i = 0; i < 40; i++) {
  try {
    const pages = await list();
    page = pages.find((p) => p.type === "page" && p.webSocketDebuggerUrl);
    if (page) break;
  } catch {}
  await sleep(250);
}
if (!page) {
  console.error("no page");
  process.exit(2);
}
const ws = new WebSocket(page.webSocketDebuggerUrl);
let id = 0;
const pending = new Map();
function send(method, params = {}) {
  const msgId = ++id;
  ws.send(JSON.stringify({ id: msgId, method, params }));
  return new Promise((resolve, reject) => pending.set(msgId, { resolve, reject }));
}
ws.addEventListener("message", (ev) => {
  const msg = JSON.parse(ev.data);
  if (msg.id && pending.has(msg.id)) {
    const { resolve, reject } = pending.get(msg.id);
    pending.delete(msg.id);
    if (msg.error) reject(new Error(JSON.stringify(msg.error)));
    else resolve(msg.result);
  }
});
await new Promise((resolve) => ws.addEventListener("open", resolve));
await send("Runtime.enable");
await sleep(1500);
await send("Runtime.evaluate", {
  expression: `(() => { window.__n = 0; const f = () => { window.__n++; requestAnimationFrame(f); }; requestAnimationFrame(f); return location.href; })()`,
  returnByValue: true,
});
await sleep(2000);
const result = await send("Runtime.evaluate", { expression: "window.__n", returnByValue: true });
console.log(JSON.stringify({ url: target, href: page.url, frames: result.result.value, seconds: 2 }));
ws.close();
