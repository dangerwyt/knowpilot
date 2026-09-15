/**
 * T89a 前端验收：报告页的「无资料警告条」显示得对不对
 *
 * 为什么单独验这一层
 * ------------------
 * 后端判据只证明「结论落库了」；vue-tsc EXIT=0 只证明「编译过」。
 * 警告条会不会在不该出现的时候出现，只有打开真浏览器看才知道。
 *
 * 三组对照（这是本脚本的重点）
 * ---------------------------
 *   has_material = false  → 必须显示警告条
 *   has_material = true   → 必须不显示        （对照 1：别误报）
 *   has_material 无该键   → 必须不显示        （对照 2：三态！用 !x 写法这条必挂）
 *
 * 判据
 * ----
 * F1 三份报告都能打开、无 JS 报错
 * F2 false 那份：警告条可见，文案含「资料」与「模型知识」
 * F3 true 那份：不显示警告条
 * F4 无键那份：不显示警告条（三态语义的判据）
 * F5 警告条没把正文顶掉（该报告仍有章节标题）
 *
 * 跑法（需要 vite 在 5173、uvicorn 在 8000）
 *   NODE_PATH=D:/nvm/node_global/node_modules node t89a_frontend_has_material_test.js
 */
const puppeteer = require("puppeteer");
const { spawnSync } = require("child_process");

const BASE = "http://localhost:5173";
const API = "http://localhost:8000";
const CHROME = "C:/Program Files/Google/Chrome/Application/chrome.exe";
const SHOT_DIR = "c:/Users/dange/WorkBuddy/agent应用开发/.workbuddy/tmp";
const ALERT_SEL = ".no-material-alert";

const RESULTS = [];
function record(cid, ok, detail) {
  const tag = ok === null ? "SKIP" : ok ? "PASS" : "FAIL";
  RESULTS.push([cid, tag, detail]);
  console.log(`[${tag}] ${cid}  ${detail}`);
}

/**
 * 用 spawnSync + 参数数组调 psql。
 * 不能用 execSync(`... -c "${sql}"`)：Node 在 Windows 上走 cmd /c，
 * SQL 里的 `->>` / `?` / `||` 会被 cmd 吃掉，SQL 被静默截断。
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

/** 按 quality.has_material 取值挑出三份被测报告（false / true / 无键） */
function pickReports() {
  const rows = psql(
    "select r.id, coalesce(r.content->'quality'->>'has_material','<无键>'), " +
      "coalesce(jsonb_typeof(r.content->'quality'->'has_material'),'none') " +
      "from reports r order by r.created_at desc;"
  )
    .split("\n")
    .filter(Boolean)
    .map((l) => {
      const p = l.split("|");
      return { id: p[0], flag: p[1], jtype: p[2] };
    });
  return {
    all: rows,
    f: rows.find((r) => r.flag === "false") || null,
    t: rows.find((r) => r.flag === "true") || null,
    none: rows.find((r) => r.flag === "<无键>") || null,
  };
}

/** 造一份「没有 has_material 键」的临时报告（只用于对照，测完删） */
function makeNoKeyProbe(srcId) {
  const id = psql(
    "insert into reports (id, task_id, title, content, version, created_at) " +
      `select gen_random_uuid(), task_id, '[T89A-PROBE]' || title, ` +
      "content #- '{quality,has_material}' #- '{quality,material_count}', 97, now() " +
      `from reports where id = '${srcId}' returning id;`
  );
  return id.split("\n")[0].trim();
}

function dropNoKeyProbe() {
  const n = psql("delete from reports where version = 97 and title like '[T89A-PROBE]%';");
  return n;
}

/** 打开报告页，读警告条的可见性 / 文案 / 章节数 */
async function inspect(page, reportId) {
  const errs = [];
  const onErr = (e) => errs.push(String(e));
  page.on("pageerror", onErr);
  await page.goto(`${BASE}/reports/${reportId}`, { waitUntil: "networkidle2" });
  await page.waitForSelector(".report-card", { timeout: 15000 });
  const got = await page.evaluate((sel) => {
    const el = document.querySelector(sel);
    const visible = !!el && el.offsetParent !== null && el.getBoundingClientRect().height > 4;
    return {
      exists: !!el,
      visible,
      text: el ? el.innerText.replace(/\s+/g, " ").trim() : "",
      hasRerunBtn: el
        ? [...el.querySelectorAll("button")].some((b) => b.innerText.includes("重新调研"))
        : false,
      sectionCount: document.querySelectorAll("h2.section-title").length,
      pageText: document.body.innerText.slice(0, 200),
    };
  }, ALERT_SEL);
  page.off("pageerror", onErr);
  return { ...got, errs };
}

(async () => {
  const picked = pickReports();
  console.log("库里的报告（真相源）：");
  for (const r of picked.all) {
    console.log(`  ${r.id.slice(0, 8)}  has_material=${r.flag}  jsonb类型=${r.jtype}`);
  }
  if (!picked.f || !picked.t) {
    console.log(
      `\n需要 2 份报告做对照：has_material=false（现在 ${picked.f ? "有" : "缺"}）、` +
        `true（现在 ${picked.t ? "有" : "缺"}）。先跑 t89a_has_material_test.py 发两个任务。`
    );
    process.exit(2);
  }

  // 无键对照组：优先用库里现存的老报告（真实数据更有说服力），没有才造临时报告
  let probeId = null;
  let noneId = picked.none ? picked.none.id : null;
  let noneFrom = picked.none ? "库里现存老报告" : "（无）";
  if (!noneId) {
    probeId = makeNoKeyProbe(picked.t.id);
    noneId = probeId;
    noneFrom = "临时探针（测完删除）";
    console.log(`\n库里没有「无键」报告了，临时造一份：${probeId.slice(0, 8)}`);
  }

  const lr = await fetch(`${API}/api/v1/auth/login`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ email: "kptest@example.com", password: "test123456" }),
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
  await page.goto(`${BASE}/login`, { waitUntil: "domcontentloaded" });
  await page.evaluate(
    (t, u) => {
      localStorage.setItem("token", t);
      localStorage.setItem("user", u);
    },
    token,
    JSON.stringify(user)
  );

  console.log("\n页面实测：");
  const rFalse = await inspect(page, picked.f.id);
  console.log(
    `  false 那份：警告条 exists=${rFalse.exists} visible=${rFalse.visible} ` +
      `文案="${rFalse.text.slice(0, 46)}" 章节=${rFalse.sectionCount}`
  );
  const rTrue = await inspect(page, picked.t.id);
  console.log(`  true 那份：警告条 exists=${rTrue.exists} visible=${rTrue.visible} 章节=${rTrue.sectionCount}`);
  const rNone = await inspect(page, noneId);
  console.log(
    `  无键那份：警告条 exists=${rNone.exists} visible=${rNone.visible}（来源：${noneFrom}）章节=${rNone.sectionCount}`
  );

  const allErrs = [...rFalse.errs, ...rTrue.errs, ...rNone.errs];
  record("F1", allErrs.length === 0,
    allErrs.length ? `页面有 JS 报错：${allErrs[0]}` : "三份报告都打开、无 JS 报错");

  const textOk = rFalse.text.includes("资料") && rFalse.text.includes("模型知识");
  record("F2", rFalse.visible && textOk,
    rFalse.visible
      ? (textOk ? `警告条显示且文案到位："${rFalse.text.slice(0, 48)}"`
                : `警告条显示了但文案不含关键词（实际 "${rFalse.text}"）`)
      : `has_material=false 却没有可见的警告条（exists=${rFalse.exists}）`);

  record("F3", !rTrue.visible,
    rTrue.visible ? `has_material=true 也弹了警告条 —— 这是误报（文案 "${rTrue.text}"）`
                  : "对照 1：has_material=true 不显示警告条");

  record("F4", !rNone.visible,
    rNone.visible ? "对照 2：报告里没有 has_material 键却弹了警告条 —— 用 !x 写法的典型漏判"
                  : `对照 2：无该键时不显示警告条（来源：${noneFrom}）`);

  record("F5", rFalse.sectionCount > 0,
    rFalse.sectionCount > 0 ? `警告条没把正文顶掉（该报告仍有 ${rFalse.sectionCount} 个章节）`
                            : "警告条出现后报告正文是空的");

  // 截图：显示 / 不显示各一张
  await page.goto(`${BASE}/reports/${picked.f.id}`, { waitUntil: "networkidle2" });
  await page.waitForSelector(".report-card", { timeout: 15000 });
  await new Promise((r) => setTimeout(r, 600));
  await page.screenshot({ path: `${SHOT_DIR}/t89a_with_warning.png` });
  await page.goto(`${BASE}/reports/${picked.t.id}`, { waitUntil: "networkidle2" });
  await page.waitForSelector(".report-card", { timeout: 15000 });
  await new Promise((r) => setTimeout(r, 600));
  await page.screenshot({ path: `${SHOT_DIR}/t89a_without_warning.png` });
  console.log(`\n截图：${SHOT_DIR}/t89a_with_warning.png / t89a_without_warning.png`);

  await browser.close();

  // 清理临时探针（只删自己造的那一行，按固定标记 + version=97）
  if (probeId) {
    const n = dropNoKeyProbe();
    const left = psql("select count(*) from reports where version = 97 and title like '[T89A-PROBE]%';");
    console.log(`临时探针清理：删除 ${n.trim()} 行，残留 ${left} 行`);
  } else {
    console.log("临时探针：未造（用的是库里现存老报告），无需清理");
  }

  const nPass = RESULTS.filter((r) => r[1] === "PASS").length;
  const nFail = RESULTS.filter((r) => r[1] === "FAIL").length;
  console.log(`\n  PASS=${nPass}  FAIL=${nFail}`);
  process.exit(nFail ? 1 : 0);
})();
