/**
 * T88 前端验收：报告页质检弹窗里的「分项分数」有没有真渲染出来
 *
 * 为什么单独验这一层
 * ------------------
 * 后端 15 条判据全 PASS + 报告 JSON 里 dimensions 也对，只能证明「数据到了前端」；
 * vue-tsc EXIT=0 只能证明「编译过」。分项到底有没有画在页面上、四个数字对不对，
 * 必须打开真浏览器看。
 *
 * 判据（与 DB 逐项比对，DB 是真相源）
 * ----------------------------------
 * F1 两份报告都能打开、无 JS 报错
 * F2 点「评审意见」后弹窗打开
 * F3 弹窗里有 4 行分项
 * F4 维度名与顺序 == DB 里的 dimensions
 * F5 分数文本 == DB 的 score（形如 22/25）
 * F6 进度条宽度 == round(score / 25 * 100)%
 *
 * 跑法（需要 vite 在 5173、uvicorn 在 8000）
 *   KP_TEST_EMAIL=<测试账号> KP_TEST_PASSWORD=<口令> \
 *     NODE_PATH=D:/nvm/node_global/node_modules node t88_frontend_dimensions_test.js
 *
 * 测试账号走环境变量：公开仓库里不留明文口令（t83 的 C7 判据会扫出来）。
 * 本地库没有这个账号就先注册：POST /api/v1/auth/register
 */
const puppeteer = require("puppeteer");
const { spawnSync } = require("child_process");

const BASE = "http://localhost:5173";
const API = "http://localhost:8000";
const CHROME = "C:/Program Files/Google/Chrome/Application/chrome.exe";
const SHOT_DIR = "c:/Users/dange/WorkBuddy/agent应用开发/.workbuddy/tmp";
const MAX_PER_DIM = 25;

// 本地测试账号走环境变量：公开仓库里不留明文口令（t83 的 C7 判据会扫）。
// 放在模块顶层 —— 环境没配好就立刻报错退出，不必先跑一趟 DB 查询才发现。
const TEST_EMAIL = process.env.KP_TEST_EMAIL;
const TEST_PASSWORD = process.env.KP_TEST_PASSWORD;
if (!TEST_EMAIL || !TEST_PASSWORD) {
  console.error(
    "缺少 KP_TEST_EMAIL / KP_TEST_PASSWORD。示例：\n" +
      "  KP_TEST_EMAIL=<测试账号> KP_TEST_PASSWORD=<口令> " +
      "NODE_PATH=D:/nvm/node_global/node_modules node <本脚本>"
  );
  process.exit(2);
}

const RESULTS = [];
function record(cid, ok, detail) {
  const tag = ok === null ? "SKIP" : ok ? "PASS" : "FAIL";
  RESULTS.push([cid, tag, detail]);
  console.log(`[${tag}] ${cid}  ${detail}`);
}

/**
 * 用 spawnSync + 参数数组调 psql。
 * 注意：不能用 execSync(`... -c "${sql}"`) —— Node 在 Windows 上走 cmd /c，
 * SQL 里的 `->>` / `||` 会被 cmd 当成重定向和条件操作符，SQL 被静默截断
 * （报 "syntax error at end of input"，很难看出是 shell 的问题）。
 */
function psql(sql) {
  const r = spawnSync(
    "docker",
    ["exec", "knowpilot-postgres-1", "psql", "-U", "knowpilot", "-d", "knowpilot", "-t", "-A", "-c", sql],
    { encoding: "utf-8" }
  );
  if (r.status !== 0) throw new Error("psql 执行失败：" + (r.stderr || r.error));
  return r.stdout.trim();
}

/** 从 DB 取最近两份报告 + 各自的分项（真相源） */
function loadReports() {
  // psql -A 默认用 | 分隔多列 —— 让 DB 自己分列，别在 SQL 里拼字符串
  const raw = psql(
    "select r.id, t.id, coalesce(r.content->'quality'->>'score',''), " +
      "coalesce((r.content->'quality'->'dimensions')::text,'[]') " +
      "from reports r join tasks t on t.id = r.task_id order by r.created_at desc limit 2;"
  );
  return raw
    .split("\n")
    .filter(Boolean)
    .map((line) => {
      const parts = line.split("|");
      const dimsText = parts.slice(3).join("|"); // comment 里可能有 |，从第 4 段起整段都是 JSON
      let dims = [];
      try {
        dims = JSON.parse(dimsText || "[]");
      } catch (e) {
        dims = [];
      }
      return { reportId: parts[0], taskId: parts[1], score: Number(parts[2]), dims };
    });
}

/** 打开某份报告的「评审意见」弹窗，读回分项渲染结果 */
async function readDimensions(page, reportId) {
  await page.goto(`${BASE}/reports/${reportId}`, { waitUntil: "networkidle2" });
  await page.waitForSelector(".report-card", { timeout: 15000 });

  // 点标题栏里的「评审意见（N）」
  const clicked = await page.evaluate(() => {
    const btns = [...document.querySelectorAll("button")];
    const b = btns.find((x) => x.innerText.includes("评审意见"));
    if (!b) return false;
    b.click();
    return true;
  });
  if (!clicked) return { error: "页面上找不到「评审意见」按钮" };

  await page.waitForFunction(
    () =>
      [...document.querySelectorAll(".el-dialog")].some(
        (d) => d.offsetParent !== null && d.innerText.includes("质检评审意见")
      ),
    { timeout: 10000 }
  );

  return await page.evaluate(() => {
    const dlg = [...document.querySelectorAll(".el-dialog")].find(
      (d) => d.offsetParent !== null && d.innerText.includes("质检评审意见")
    );
    // 分项行 = 弹窗里的每个 el-progress 所在的容器
    const rows = [...dlg.querySelectorAll(".el-progress")].map((p) => {
      const row = p.parentElement;
      const spanTexts = [...row.querySelectorAll("span")]
        .map((s) => s.textContent.trim())
        .filter(Boolean);
      const inner = p.querySelector(".el-progress-bar__inner");
      const width = inner ? inner.style.width : null;
      return {
        name: spanTexts[0] || null,
        scoreText: spanTexts[spanTexts.length - 1] || null,
        width,
        ariaNow: p.getAttribute("aria-valuenow"),
      };
    });
    return { rows, dialogText: dlg.innerText.slice(0, 120) };
  });
}

(async () => {
  const reports = loadReports();
  if (reports.length < 2) {
    console.log(`数据库里只有 ${reports.length} 份报告，需要 2 份做对照`);
    process.exit(2);
  }
  console.log("被测报告（DB 真相源）：");
  for (const r of reports) {
    console.log(
      `  ${r.reportId.slice(0, 8)}  score=${r.score}  dimensions=${r.dims.length} 项 ` +
        r.dims.map((d) => `${d.name}:${d.score}`).join(" / ")
    );
  }

  // 登录换 token
  // 登录换 token（测试账号来自文件顶层的 KP_TEST_EMAIL / KP_TEST_PASSWORD）
  const lr = await fetch(`${API}/api/v1/auth/login`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ email: TEST_EMAIL, password: TEST_PASSWORD }),
  });
  const { token, user } = await lr.json();
  if (!token) throw new Error("登录失败");

  const browser = await puppeteer.launch({
    executablePath: CHROME,
    headless: "new",
    args: ["--no-sandbox", "--window-size=1600,1000"],
  });
  const page = await browser.newPage();
  await page.setViewport({ width: 1600, height: 1000 });

  const errs = [];
  page.on("pageerror", (e) => errs.push(String(e)));

  await page.goto(`${BASE}/login`, { waitUntil: "domcontentloaded" });
  await page.evaluate(
    (t, u) => {
      localStorage.setItem("token", t);
      localStorage.setItem("user", u);
    },
    token,
    JSON.stringify(user)
  );

  let allRowsOk = true;
  let allNameOk = true;
  let allScoreOk = true;
  let allWidthOk = true;
  let detailLines = [];

  for (const r of reports) {
    const got = await readDimensions(page, r.reportId);
    if (got.error) {
      record(`F2-${r.reportId.slice(0, 8)}`, false, got.error);
      continue;
    }
    const rows = got.rows;
    detailLines.push(
      `  ${r.reportId.slice(0, 8)}：页面渲染 ${rows.length} 行 → ` +
        rows.map((x) => `${x.name}=${x.scoreText}(${x.width})`).join(" / ")
    );

    // F3 行数
    if (rows.length !== 4) allRowsOk = false;

    // F4 维度名与顺序
    const names = rows.map((x) => x.name);
    const dbNames = r.dims.map((d) => d.name);
    if (JSON.stringify(names) !== JSON.stringify(dbNames)) allNameOk = false;

    // F5 分数文本
    const scoreTexts = rows.map((x) => x.scoreText);
    const dbTexts = r.dims.map((d) => `${d.score}/${MAX_PER_DIM}`);
    if (JSON.stringify(scoreTexts) !== JSON.stringify(dbTexts)) allScoreOk = false;

    // F6 进度条宽度
    const widths = rows.map((x) => x.width);
    const dbWidths = r.dims.map((d) => `${Math.round((d.score / MAX_PER_DIM) * 100)}%`);
    if (JSON.stringify(widths) !== JSON.stringify(dbWidths)) allWidthOk = false;
  }

  console.log("页面实测：");
  detailLines.forEach((l) => console.log(l));

  record("F1", errs.length === 0, errs.length ? `页面有 JS 报错：${errs[0]}` : "两份报告都打开、无 JS 报错");
  record("F3", allRowsOk, allRowsOk ? "两份都渲染出 4 行分项" : "有报告渲染的行数不是 4");
  record("F4", allNameOk, allNameOk ? "维度名与顺序和 DB 一致" : "维度名/顺序与 DB 不一致");
  record("F5", allScoreOk, allScoreOk ? "分数文本与 DB 一致（形如 22/25）" : "分数文本与 DB 不一致");
  record("F6", allWidthOk, allWidthOk ? "进度条宽度 == round(score/25*100)%" : "进度条宽度与分数不匹配");

  // 截图存档：单独重新打开一份报告 + 点开弹窗再截
  // （循环结束时刚做过 goto，弹窗状态已被重置，直接截会截到空报告页）
  await page.goto(`${BASE}/reports/${reports[0].reportId}`, { waitUntil: "networkidle2" });
  await page.waitForSelector(".report-card", { timeout: 15000 });
  await page.evaluate(() => {
    const b = [...document.querySelectorAll("button")].find((x) =>
      x.innerText.includes("评审意见")
    );
    if (b) b.click();
  });
  await new Promise((r) => setTimeout(r, 900)); // 等弹窗过渡动画结束
  const shot = `${SHOT_DIR}/t88_dimensions_dialog.png`;
  await page.screenshot({ path: shot, fullPage: false });
  console.log(`截图：${shot}`);

  await browser.close();

  const nPass = RESULTS.filter((r) => r[1] === "PASS").length;
  const nFail = RESULTS.filter((r) => r[1] === "FAIL").length;
  console.log(`\n  PASS=${nPass}  FAIL=${nFail}`);
  process.exit(nFail ? 1 : 0);
})();
