/* 대시보드 스크립트를 최소 DOM 흉내 위에서 실제로 돌려 본다.
   화면을 안 띄우고도 런타임 오류(오타 하나로 스크립트 전체가 죽는 일)를 잡는다.
   사용: node scripts/domcheck.mjs [dashboard/index.html] */
import { readFileSync } from "node:fs";
import vm from "node:vm";

const file = process.argv[2] || new URL("../dashboard/index.html", import.meta.url).pathname;
const html = readFileSync(file, "utf8");

const payload = /<script id="payload"[^>]*>([\s\S]*?)<\/script>/.exec(html);
const app = [...html.matchAll(/<script>([\s\S]*?)<\/script>/g)].map(m => m[1]).join("\n");
if (!payload || !app.trim()) { console.error("스크립트를 찾지 못했습니다"); process.exit(1); }

function run(seed) {
let nodeCount = 0;
function mkNode(tag) {
  const n = {
    tagName: String(tag).toUpperCase(), _tag: tag, _id: ++nodeCount,
    children: [], attrs: {}, style: {}, dataset: {}, className: "", textContent: "",
    clientWidth: 900, parentNode: null,
    setAttribute(k, v) { this.attrs[k] = String(v); if (k.startsWith("data-")) this.dataset[k.slice(5)] = String(v); },
    getAttribute(k) { return k in this.attrs ? this.attrs[k] : null; },
    removeAttribute(k) { delete this.attrs[k]; },
    append(...ks) { for (const k of ks) if (k && typeof k === "object") { k.parentNode = this; this.children.push(k); } },
    appendChild(k) { this.append(k); return k; },
    replaceChildren(...ks) { this.children = []; this.append(...ks); },
    addEventListener() {}, removeEventListener() {},
    closest() { return null; },
    querySelector() { return null; },
    querySelectorAll() { return []; },
    getBoundingClientRect() { return { left: 0, top: 0, width: 900, height: 300 }; },
    classList: { add() {}, remove() {}, toggle() {} },
    get firstChild() { return this.children[0] || null; },
  };
  n.style.cssText = "";
  n.style.setProperty = () => {};
  return n;
}

const byId = new Map();
const need = [...html.matchAll(/id="([^"]+)"/g)].map(m => m[1]);
for (const id of need) byId.set(id, mkNode("div"));
byId.get("payload").textContent = payload[1].replace(/<\\\//g, "</");

const root = mkNode("html");
const main = mkNode("main");
const document_ = {
  documentElement: root,
  createElement: mkNode,
  createElementNS: (_ns, t) => mkNode(t),
  createTextNode: t => ({ textContent: String(t), children: [] }),
  getElementById: id => byId.get(id) || null,
  querySelector: sel => (sel === "main" ? main : (sel === ".quick" ? mkNode("div") : null)),
  querySelectorAll: () => [],
  addEventListener() {},
};

const store = new Map(seed ? [["naver-monitor-dashboard-v1", JSON.stringify(seed)]] : []);
const ctx = {
  document: document_, window: null, console,
  localStorage: { getItem: k => (store.has(k) ? store.get(k) : null),
                  setItem: (k, v) => store.set(k, String(v)), removeItem: k => store.delete(k) },
  innerWidth: 1400, innerHeight: 900, scrollX: 0, scrollY: 0,
  addEventListener() {}, setTimeout: () => 0, clearTimeout() {},
  matchMedia: () => ({ matches: false, addEventListener() {} }),
  JSON, Math, Date, Object, Array, Map, Set, String, Number, Intl, isNaN, parseInt, parseFloat,
};
ctx.window = ctx; ctx.globalThis = ctx;
vm.createContext(ctx);

try {
  vm.runInContext(app, ctx, { filename: "dashboard.js" });
} catch (e) {
  console.error("✗ 대시보드 스크립트가 죽었습니다:\n  " + (e && e.stack || e));
  process.exit(1);
}

const svgsIn = id => {
  const box = byId.get(id);
  const walk = n => (n._tag === "svg" ? 1 : 0) + (n.children || []).reduce((a, c) => a + walk(c), 0);
  return box ? walk(box) : 0;
};
const checks = [
  ["dot-facets", "호가와 등록 추이"],
  ["hist-facets", "매물 분포"],
  ["trend-facets", "물량 추이"],
  ["qt-facets", "조사일별 호가"],
  ["flow-facets", "조사일별 증감"],
];
  const svgs = Object.fromEntries(checks.map(([id, label]) => [label, svgsIn(id)]));
  const rows = (byId.get("table")?.children[0]?.children[1]?.children || []).length;
  const cxbar = (byId.get("cxbar")?.children || [])
    .filter(n => n._tag === "button")
    .map(n => ({ cx: n.dataset.cx, on: n.getAttribute("aria-pressed") === "true",
                 label: (n.children[1] || {}).textContent }));
  return { svgs, rows, cxbar };
}

let bad = 0;
const base = run(null);
for (const [label, n] of Object.entries(base.svgs)) {
  console.log(`  ${n > 0 ? "✓" : "✗"} ${label}: svg ${n}개`);
  if (n === 0) bad++;
}
console.log(`  ${base.rows > 0 ? "✓" : "✗"} 물건 목록: ${base.rows}행`);
if (base.rows === 0) bad++;

// 범례는 스위치다 — 목록에 다 남아 있으면서, 끈 단지는 화면에서 빠져야 한다
if (!base.cxbar.length) { console.log("  ✗ 단지 범례: 비어 있음"); bad++; }
else {
  const off = base.cxbar[0].cx;
  const dim = run({ hidden: [off] });
  const stillListed = dim.cxbar.length === base.cxbar.length;
  const pressed = dim.cxbar.find(b => b.cx === off)?.on;
  const shrank = dim.rows < base.rows;
  const ok = stillListed && pressed === false && shrank;
  console.log(`  ${ok ? "✓" : "✗"} 단지 범례 ${base.cxbar.length}개 · `
    + `"${base.cxbar[0].label}" 끄면 ${base.rows}행 → ${dim.rows}행`
    + (stillListed ? "" : " (목록에서 사라짐!)"));
  if (!ok) bad++;
}
console.log(bad ? `✗ 실패 ${bad}건` : "✓ 모든 차트가 그려졌습니다");
process.exit(bad ? 1 : 0);
