/**
 * T89b 前端验收：发起前的预检确认框
 *
 * 为什么用「请求拦截」而不是发真任务
 * --------------------------------
 * 要验的核心是「点了哪个按钮，会不会真的发出任务」——这问题用观察请求就能回答，
 * 没必要真的跑一遍调研（要 1 分钟 + 产生垃圾数据）。所以：
 *   · POST /tasks/precheck  → 放行（真调后端，验真实预检）
 *   · POST /tasks           → 拦截并回 201 假任务（只记录"发出过"，不落库）
 *   · 其余请求              → 放行
 *
 * 判据（每个场景都重新加载页面）
 * -----------------------------
 *   D1 无关目标 + 选了知识库 → 弹确认框，文案含「资料」
 *   D2 点「先去补资料」      → **没有**发出 POST /tasks   ← 反向判据，重点
 *   D3 点「先去补资料」      → 跳到知识库页
 *   D4 点「仍要继续」        → 发出了 POST /tasks（1 次）
 *   D5 相关目标 + 选了知识库 → 不弹框、直接发起，且 precheck 调了 1 次
 *   D6 不选知识库            → **不调** precheck、直接发起（没 kb 不该去预检）
 *   E1 场景5 真发无资料任务   → 时间线里 probe 那行是 warning 色   ← 需真任务
 *   E2 同一次任务里的其它行   → 不是 warning（对照：证明不是"全变黄"）
 *
 * 为什么 E 组非要真任务
 * --------------------
 * probe 事件只在任务真跑起来时才推给前端，mock 不出来。E2 是对照组：
 * 只验「probe 行是黄的」的话，一个「所有行都变黄」的实现也能通过。
 *
 * 跑法（需要 vite:5173 + uvicorn:8000；E 组还会真发一个任务）
 *   NODE_PATH=D:/nvm/node_global/node_modules node t89b_frontend_precheck_test.js
 *   NODE_PATH=D:/nvm/node_global/node_modules node t89b_frontend_precheck_test.js --skip-slow
 */
const puppeteer = require("puppeteer");

const BASE = "http://localhost:5173";
const API = "http://localhost:8000";
const CHROME = "C:/Program Files/Google/Chrome/Application/chrome.exe";
const SHOT_DIR = "c:/Users/dange/WorkBuddy/agent应用开发/.workbuddy/tmp";

const OBJ_WITH = "知研 KnowPilot 是什么？";
const OBJ_WITHOUT = "2026 年国内新能源汽车销量排名";
const KB_NAME = "产品文档库";
const SKIP_SLOW = process.argv.includes("--skip-slow");

const calls = { precheck: [], createTask: [], realTaskId: null };
/** 场景 1-4 拦掉 POST /tasks（只看"发没发"）；场景 5 放行（要真任务） */
let allowRealTask = false;
const RESULTS = [];
function record(cid, ok, detail) {
  const tag = ok === null ? "SKIP" : ok ? "PASS" : "FAIL";
  RESULTS.push([cid, tag, detail]);
  console.log(`[${tag}] ${cid}  ${detail}`);
}

const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

/** 点 el-select 选中知识库（多选下拉） */
async function pickKb(page) {
  await page.click(".el-select");
  await page.waitForSelector(".el-select-dropdown__item", { timeout: 8000, visible: true });
  await page.evaluate(() => {
    const items = [...document.querySelectorAll(".el-select-dropdown__item")];
    const hit = items.find((i) => i.innerText.includes("产品文档库")) || items[0];
    hit.click();
  });
  await page.keyboard.press("Escape"); // 多选下拉不会自动关，不关会盖住按钮
  await sleep(300);
}

/** 填目标 + 点「开始调研」 */
async function fillAndSubmit(page, objective) {
  await page.waitForSelector("textarea.el-textarea__inner", { timeout: 10000 });
  await page.click("textarea.el-textarea__inner");
  await page.evaluate((t) => {
    const ta = document.querySelector("textarea.el-textarea__inner");
    ta.value = t;
    ta.dispatchEvent(new Event("input", { bubbles: true }));
  }, objective);
  await sleep(200);
  await page.evaluate(() => {
    const b = [...document.querySelectorAll("button")].find((x) => x.innerText.includes("开始调研"));
    if (b) b.click();
  });
}

/** 等确认框出现；返回文案 */
async function waitConfirmBox(page, timeout = 15000) {
  try {
    await page.waitForFunction(
      () =>
        [...document.querySelectorAll(".el-message-box")].some(
          (d) => d.offsetParent !== null
        ),
      { timeout }
    );
  } catch (e) {
    return null;
  }
  return await page.evaluate(() => {
    const box = [...document.querySelectorAll(".el-message-box")].find((d) => d.offsetParent !== null);
    return {
      text: box ? box.innerText.replace(/\s+/g, " ").trim() : "",
      buttons: box ? [...box.querySelectorAll("button")].map((b) => b.innerText.trim()) : [],
    };
  });
}

async function clickBoxButton(page, label) {
  return await page.evaluate((l) => {
    const box = [...document.querySelectorAll(".el-message-box")].find((d) => d.offsetParent !== null);
    if (!box) return false;
    const b = [...box.querySelectorAll("button")].find((x) => x.innerText.includes(l));
    if (!b) return false;
    b.click();
    return true;
  }, label);
}

(async () => {
  const lr = await fetch(`${API}/api/v1/auth/login`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ email: "kptest@example.com", password: "test123456" }),
  });
  const { token, user } = await lr.json();
  if (!token) throw new Error("登录失败");
  const H = { Authorization: `Bearer ${token}` };
  const projects = await (await fetch(`${API}/api/v1/projects`, { headers: H })).json();
  const kbs = await (await fetch(`${API}/api/v1/knowledge-bases`, { headers: H })).json();
  const kb = kbs.find((k) => k.name === KB_NAME);
  if (!projects.length || !kb) throw new Error(`前置不足：projects=${projects.length} kb=${!!kb}`);
  const projectId = projects[0].id;
  console.log(`项目 ${projectId.slice(0, 8)} / 知识库「${kb.name}」(${kb.id.slice(0, 8)})\n`);

  const browser = await puppeteer.launch({
    executablePath: CHROME,
    headless: "new",
    args: ["--no-sandbox", "--window-size=1600,1000"],
  });
  const page = await browser.newPage();
  await page.setViewport({ width: 1600, height: 1000 });
  const errs = [];
  page.on("pageerror", (e) => errs.push(String(e)));
  // 场景 5 要拿到真任务 id（用于事后清理）；假任务那条不会命中，因为被 respond 拦掉了
  page.on("response", async (res) => {
    try {
      if (allowRealTask && res.request().method() === "POST" && /\/api\/v1\/tasks$/.test(res.url())) {
        calls.realTaskId = (await res.json()).id;
      }
    } catch (e) { /* 非 JSON 响应，忽略 */ }
  });

  await page.setRequestInterception(true);
  page.on("request", (req) => {
    const url = req.url();
    const method = req.method();
    if (url.includes("/api/v1/tasks/precheck")) {
      calls.precheck.push(req.postData() || "");
      return req.continue();
    }
    if (method === "POST" && /\/api\/v1\/tasks$/.test(url)) {
      calls.createTask.push(req.postData() || "");
      if (allowRealTask) return req.continue();
      return req.respond({
        status: 201,
        contentType: "application/json",
        body: JSON.stringify({
          id: "00000000-0000-0000-0000-0000000000ff",
          project_id: projectId,
          objective: "mock",
          status: "pending",
          report_id: null,
          created_at: new Date().toISOString(),
        }),
      });
    }
    return req.continue();
  });

  await page.goto(`${BASE}/login`, { waitUntil: "domcontentloaded" });
  await page.evaluate(
    (t, u) => {
      localStorage.setItem("token", t);
      localStorage.setItem("user", u);
    },
    token,
    JSON.stringify(user)
  );

  const WB = `${BASE}/projects/${projectId}`;
  let confirmText = "";

  // ---------- 场景 1：无关目标 + 选了 kb → 应弹框，点「先去补资料」
  calls.precheck.length = 0;
  calls.createTask.length = 0;
  await page.goto(WB, { waitUntil: "networkidle2" });
  await pickKb(page);
  await fillAndSubmit(page, OBJ_WITHOUT);
  const box1 = await waitConfirmBox(page);
  confirmText = box1 ? box1.text : "";
  console.log(`场景1 确认框：${box1 ? `"${box1.text.slice(0, 60)}" 按钮=${box1.buttons}` : "（没出现）"}`);
  if (box1) {
    await page.screenshot({ path: `${SHOT_DIR}/t89b_precheck_confirm.png` });
    await clickBoxButton(page, "先去补资料");
    await sleep(900);
  }
  const afterCancel = { precheck: calls.precheck.length, createTask: calls.createTask.length, url: page.url() };
  console.log(`场景1 点取消后：precheck=${afterCancel.precheck} createTask=${afterCancel.createTask} url=${afterCancel.url}`);

  record("D1", !!box1 && confirmText.includes("资料"),
    box1 ? `确认框出现，文案含「资料」："${confirmText.slice(0, 52)}"`
          : "无关目标 + 选了知识库，却没弹确认框");
  record("D2", afterCancel.createTask === 0,
    afterCancel.createTask === 0
      ? "点「先去补资料」后**没有**发出 POST /tasks（没偷偷发起）"
      : `点「先去补资料」后仍发出 ${afterCancel.createTask} 次 POST /tasks —— 用户以为取消了，任务却跑起来了`);
  record("D3", afterCancel.url.includes("/knowledge-bases"),
    afterCancel.url.includes("/knowledge-bases") ? "点「先去补资料」跳到了知识库页"
      : `点「先去补资料」没跳转（当前 ${afterCancel.url}）`);

  // ---------- 场景 2：同样输入，点「仍要继续」
  calls.precheck.length = 0;
  calls.createTask.length = 0;
  await page.goto(WB, { waitUntil: "networkidle2" });
  await pickKb(page);
  await fillAndSubmit(page, OBJ_WITHOUT);
  const box2 = await waitConfirmBox(page);
  if (box2) {
    await clickBoxButton(page, "仍要继续");
    await sleep(900);
  }
  console.log(`场景2 点继续后：precheck=${calls.precheck.length} createTask=${calls.createTask.length}`);
  record("D4", calls.createTask.length === 1,
    calls.createTask.length === 1 ? "点「仍要继续」后发出了 1 次 POST /tasks（确认后正常发起）"
      : `点「仍要继续」后 POST /tasks 发了 ${calls.createTask.length} 次（应为 1）`);

  // ---------- 场景 3：相关目标 + 选了 kb → 不该打扰，直接发起
  calls.precheck.length = 0;
  calls.createTask.length = 0;
  await page.goto(WB, { waitUntil: "networkidle2" });
  await pickKb(page);
  await fillAndSubmit(page, OBJ_WITH);
  const box3 = await waitConfirmBox(page, 6000);
  await sleep(600);
  console.log(`场景3（有资料）：确认框=${box3 ? "出现了" : "没出现"} precheck=${calls.precheck.length} createTask=${calls.createTask.length}`);
  record("D5", !box3 && calls.createTask.length === 1 && calls.precheck.length === 1,
    !box3
      ? `有资料时不打扰：没弹框、precheck 调了 ${calls.precheck.length} 次、发起 ${calls.createTask.length} 次`
      : `有资料也弹了确认框 —— 这是误报（文案 "${(box3.text || "").slice(0, 40)}"）`);

  // ---------- 场景 4：不选 kb → 不预检，直接发起
  calls.precheck.length = 0;
  calls.createTask.length = 0;
  await page.goto(WB, { waitUntil: "networkidle2" });
  await fillAndSubmit(page, OBJ_WITHOUT);
  const box4 = await waitConfirmBox(page, 6000);
  await sleep(600);
  console.log(`场景4（不选 kb）：确认框=${box4 ? "出现了" : "没出现"} precheck=${calls.precheck.length} createTask=${calls.createTask.length}`);
  record("D6", !box4 && calls.precheck.length === 0 && calls.createTask.length === 1,
    calls.precheck.length === 0
      ? `没选知识库时不去预检（precheck 0 次）、直接发起 ${calls.createTask.length} 次`
      : `没选知识库也调了 ${calls.precheck.length} 次 precheck —— 白等一次网络往返`);

  // ---------- 场景 5：真发一个无资料任务，看时间线里 probe 那行的颜色
  if (SKIP_SLOW) {
    record("E1", null, "--skip-slow：跳过真任务场景");
    record("E2", null, "--skip-slow：跳过真任务场景");
  } else {
    console.log("\n场景5（真发无资料任务，等 probe 事件推来，最长 4 分钟）…");
    calls.precheck.length = 0;
    calls.createTask.length = 0;
    allowRealTask = true;
    await page.goto(WB, { waitUntil: "networkidle2" });
    await pickKb(page);
    await fillAndSubmit(page, OBJ_WITHOUT);
    const box5 = await waitConfirmBox(page);
    if (box5) await clickBoxButton(page, "仍要继续");

    let gotProbe = true;
    try {
      await page.waitForFunction(
        () =>
          [...document.querySelectorAll(".el-timeline-item")].some((li) =>
            (li.innerText || "").includes("probe")
          ),
        { timeout: 240000, polling: 1000 }
      );
    } catch (e) {
      gotProbe = false;
    }

    const rows = await page.evaluate(() =>
      [...document.querySelectorAll(".el-timeline-item")].map((li) => {
        const node = li.querySelector(".el-timeline-item__node");
        return {
          text: (li.innerText || "").replace(/\s+/g, " ").trim(),
          nodeCls: node ? node.className : "",
        };
      })
    );
    await page.screenshot({ path: `${SHOT_DIR}/t89b_probe_event.png` });

    const probeRow = rows.find((r) => r.text.includes("probe"));
    const otherRow = rows.find((r) => r.text.includes("planner"));
    console.log(`场景5 任务 id=${calls.realTaskId}`);
    console.log(`场景5 时间线 ${rows.length} 行：`);
    for (const r of rows) console.log(`   · ${r.text.slice(0, 54)}  |node=${r.nodeCls}`);

    record("E1", gotProbe && !!probeRow && probeRow.nodeCls.includes("warning"),
      !gotProbe ? "等 240s 没等到 probe 那行日志（任务没推事件或页面没订阅）"
        : !probeRow ? "时间线里没有 probe 行"
          : probeRow.nodeCls.includes("warning")
            ? `probe 行是 warning 色（"${probeRow.text.slice(0, 36)}"，node 类含 warning）`
            : `probe 行**不是** warning 色：node="${probeRow.nodeCls}" —— 无资料这件事在时间线里不显眼`);

    record("E2", !!otherRow && !otherRow.nodeCls.includes("warning"),
      !otherRow ? "时间线里没找到 planner 行，对照组没法成立（先看 E1 详情）"
        : !otherRow.nodeCls.includes("warning")
          ? `对照组 OK：planner 行 node="${otherRow.nodeCls}"，没有跟着变黄`
          : `planner 行也是 warning 色 —— 前端可能把所有行都标黄了（那不是我们要的效果）`);
  }

  await browser.close();

  const nPass = RESULTS.filter((r) => r[1] === "PASS").length;
  const nFail = RESULTS.filter((r) => r[1] === "FAIL").length;
  console.log(`\n  PASS=${nPass}  FAIL=${nFail}`);
  process.exit(nFail ? 1 : 0);
})();
