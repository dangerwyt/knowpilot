/**
 * T99 前端运行时验收：图标是否注册在"真正被挂载"的那个 app 实例上
 *
 * 为什么非要开浏览器验
 * ------------------
 * B1/B2 是静态判据（数 main.ts 里 createApp 出现几次），只能证明"代码看起来没多建实例"。
 * 真正要回答的是运行时问题：**注册的图标，在我能拿到的那张 app 实例上究竟在不在？**
 * Vue 3 把 app 实例挂在容器的 __vue_app__ 上，所以直接问它就行：
 *     document.querySelector('#app').__vue_app__.component('Search')
 * 改前（注册在另一个没挂载的实例上）→ undefined；改后 → 组件定义。
 *
 * 判据
 * ----
 *   F1  #app 上有 __vue_app__（Vue 确实挂载了）
 *   F2  __vue_app__.component('Search') 有值     ← 本次修复的核心证据
 *   F3  Search / Plus / Refresh / Delete 四个图标都注册上了
 *   F4  ElButton 也在同一个实例上（Element Plus 插件装对了实例）
 *   F5  页面加载期间没有 console error / 未捕获异常
 *   S4  自检：查一个肯定没注册的名字必须返回 undefined
 *       （否则 F2 可能是"查什么都返回真"的假 PASS）
 *
 * 跑法（需要 vite:5173）
 *   NODE_PATH=D:/nvm/node_global/node_modules node t99_frontend_icon_test.js
 */
const puppeteer = require("puppeteer");

const BASE = "http://localhost:5173";
const CHROME = "C:/Program Files/Google/Chrome/Application/chrome.exe";

const RESULTS = [];
function record(cid, ok, detail) {
  RESULTS.push({ cid, ok, detail });
  console.log(`  [${ok ? "PASS" : "FAIL"}] ${cid}  ${detail}`);
}

// 探针：跑在页面上下文里，回报 __vue_app__ 上的组件注册情况
const PROBE = (names) => {
  const el = document.querySelector("#app");
  const app = el && el.__vue_app__;
  if (!app) return { hasApp: false };
  const out = {};
  for (const n of names) out[n] = !!app.component(n);
  return { hasApp: true, components: out };
};

(async () => {
  const browser = await puppeteer.launch({
    executablePath: CHROME,
    headless: "new",
    args: ["--no-sandbox"],
  });
  const page = await browser.newPage();

  const errors = [];
  page.on("console", (m) => {
    if (m.type() === "error") {
      // 带上 location.url：只说"有个 404"没法判断是不是真问题
      const loc = (m.location && m.location()) || {};
      errors.push(`${m.text().slice(0, 120)} @ ${loc.url || "?"}`);
    }
  });
  page.on("pageerror", (e) => errors.push(`[pageerror] ${String(e).slice(0, 200)}`));
  // 记录具体是哪个资源 4xx/5xx —— 只说"有个 404"没法判断是不是真问题
  const badResponses = [];
  page.on("response", (r) => {
    if (r.status() >= 400) badResponses.push(`${r.status()} ${r.url()}`);
  });

  await page.goto(`${BASE}/login`, { waitUntil: "networkidle2", timeout: 60000 });
  await page.waitForSelector("#app", { timeout: 30000 });

  const NAMES = ["Search", "Plus", "Refresh", "Delete", "ElButton", "__definitely_not_registered__"];
  const res = await page.evaluate(PROBE, NAMES);

  console.log("\n=== F 组：图标全局注册（运行时）===");
  record("F1", res.hasApp, "容器 #app 上取到了 __vue_app__ 实例");

  if (res.hasApp) {
    const c = res.components;
    record("F2", c.Search === true, `__vue_app__.component('Search') = ${c.Search}`);
    const icons = ["Search", "Plus", "Refresh", "Delete"];
    const missing = icons.filter((n) => !c[n]);
    record("F3", missing.length === 0, `四个图标都已注册；缺：${missing.length ? missing.join(",") : "无"}`);
    record("F4", c.ElButton === true, `同一实例上 Element Plus 也装好了（ElButton = ${c.ElButton}）`);
    record("S4", c.__definitely_not_registered__ === false,
      "自检：查一个不存在的组件名返回 false（证明查询本身不是恒真）");
  } else {
    record("F2", false, "没取到 __vue_app__，后续判据无法执行");
    record("F3", false, "同上");
    record("F4", false, "同上");
    record("S4", false, "同上");
  }

  // F5：加载期间的错误（favicon 的 404 是项目既有的小瑕疵，与本判据无关，单独列出不当 FAIL）
  const IGNORE = /favicon\.ico/i;
  const real = badResponses.filter((s) => !IGNORE.test(s));
  const realErrors = errors.filter((e) => !IGNORE.test(e));
  record("F5", real.length === 0 && realErrors.length === 0,
    `无资源错误（favicon 404 忽略）：资源 ${real.length ? real.slice(0, 3).join(" | ") : "无"}；console ${realErrors.length ? realErrors.slice(0, 2).join(" | ") : "无"}`);

  // F6：登录页真渲染出了表单（不是白屏/报错后的空壳）
  const painted = await page.evaluate(() => ({
    text: document.body.innerText.trim().length,
    inputs: document.querySelectorAll("input").length,
  }));
  record("F6", painted.inputs >= 2,
    `登录页渲染出 ${painted.inputs} 个输入框、正文 ${painted.text} 字符`);

  await browser.close();

  const passed = RESULTS.filter((r) => r.ok).length;
  console.log(`\n${"=".repeat(60)}\n${passed}/${RESULTS.length} PASS`);
  for (const r of RESULTS) if (!r.ok) console.log(`  FAILED ${r.cid}: ${r.detail}`);
  process.exit(passed === RESULTS.length ? 0 : 1);
})().catch((e) => {
  console.error("脚本异常：", e);
  process.exit(2);
});
