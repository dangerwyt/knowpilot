"""仓库体检：提交前跑一遍，确认 .gitignore 正反向都对、没有死引用。

判据分四组：
  G 组（gitignore 正反向）：该排的排掉、该留的留下
  W 组（白名单）：被点名引用的 playground 工具确实进了仓库；没被点名的一个都没混进来；
                 **根目录只允许登记过的条目**（W3）
  D 组（死引用）：**入库文件里引用的 playground 文件必须都在仓库里**
  C 组（内容卫生）：无 .env、无大文件、无依赖目录、关键业务文件齐全；
                   **源码/脚本里不得有写死的口令字面量**（C7）；
                   **全部历史提交里都没有敏感文件**（C8）、**也没有内部文档目录**（C9）

W3 与 C7 是「枚举式判据」的补丁（2026-09-16 加）：
  此前的判据全是「点名检查」—— 只验清单内的东西在不在，清单外的一概不看。
  结果 `_t60_fin.sh` / `_t60_retest.sh`（含明文测试口令的临时脚本）躺在仓库根目录、
  随首次全量入库（36b0990）进了公开仓库，却一条判据都没碰它。
  W3 换成「根目录白名单」：没登记 = FAIL，结构性堵住往根目录丢垃圾。
  C7 是内容兜底：文件名/目录怎么变，写死的口令都能被抓出来。
  C8 是「当前树 vs 历史」的补丁：W3/C1~C7 查的全是**当前树**，把文件删掉就全绿了 ——
  但历史提交里它还在（这正是 #103 的事故形态：文件删了、判据全 PASS、历史里却留着、
  而且已经 push 到公开仓）。C8 遍历全部历史提交的树，口径与 G1/C1/C2/C3 一致。

docs/ 退出版本控制（2026-09-17）：
  内部文档（PRD / TDD / 踩坑记录 / 部署方案 / 功能演进建议）不再随代码公开 ——
  其中部署方案含服务器公网 IP、踩坑记录含测试账号邮箱。文件仍在 knowpilot/docs/
  （本地照常维护），只是 Git 不再看它。三条判据各管一层，缺一不可：
    G3 —— gitignore 规则本身还有效（规则被改坏时能立刻发现）
    W4 —— 当前树里确实没有 docs/ 路径
    C9 —— 全部历史提交里都没有 docs/（重写 / 删库重建之后仍然成立）
  「整个目录不公开」这件事，不写成判据就等于只靠记忆维持。

D 组是核心判据 —— 仓库里存不存在「叫你去跑一个不存在的文件」的情况。
只在「会进仓库的文件」里搜引用，因为不入库的文件自己引用不到也不影响别人。

两个坑（都实测踩过，见踩坑 #94）：
  ① **必须排除脚本自身**：本脚本的判据列表里写着一堆 playground 文件名，
     不排除的话 D1 会把它们全当成"死引用"（那些未入库的探针名尤其明显）。
  ② **要排除文档里举例用的占位符**（`backend/playground/xxx.py`）：
     文档天然会写示例，判据把它们当真实引用就会假红。

用法（任意目录直接跑）：
    backend/.venv/Scripts/python.exe backend/playground/t83_git_repo_check.py
退出码 0 = 全部通过。
"""
from __future__ import annotations

import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]          # playground → backend → knowpilot

results: list[tuple[str, bool, str]] = []
skipped: list[str] = []


def check(name: str, ok: bool, detail: str = "") -> None:
    results.append((name, ok, detail))
    print(f"[{'PASS' if ok else 'FAIL'}] {name}" + (f"  {detail}" if detail else ""))


def check_skip(name: str, detail: str = "") -> None:
    skipped.append(name)
    print(f"[SKIP] {name}" + (f"  {detail}" if detail else ""))


def git(*args: str) -> str:
    p = subprocess.run(["git", *args], cwd=ROOT, capture_output=True, text=True, encoding="utf-8")
    if p.returncode != 0:
        raise RuntimeError(f"git {' '.join(args)} 失败：{p.stderr.strip()}")
    return p.stdout


def tracked_or_pending() -> list[str]:
    """已提交 + 待提交的全部文件（相对仓库根的 posix 路径）。"""
    out = git("ls-files", "-z", "--cached", "--others", "--exclude-standard")
    return sorted(x for x in out.split("\0") if x)


# ---------------------------------------------------------------- G 组
def g_group() -> None:
    def ignored(path: str) -> bool:
        p = subprocess.run(["git", "check-ignore", "-q", path], cwd=ROOT)
        return p.returncode == 0

    must_ignore = [
        ".env",
        "backend/.env",
        "backend/.venv/pyvenv.cfg",
        "frontend/node_modules/vue/index.js",
        "backend/storage/some-kb/some-doc.md",
        "backend/playground/t78_epoch_test.py",      # 验收脚本（未点名引用）
        "backend/playground/t78probe/t78_epoch_probe.md",
        "backend/playground/__pycache__/x.pyc",
        "frontend/dist/index.js",
        "frontend/vite.config.js",
        "frontend/tsconfig.app.tsbuildinfo",
        "backend/migrations/APPLIED.log",
    ]
    bad = [p for p in must_ignore if not ignored(p)]
    check("G1 [正向] 敏感文件/依赖目录/运行时产物全部被忽略", not bad,
          f"共 {len(must_ignore)} 项；漏网={bad or '无'}")

    must_keep = [
        "backend/app/main.py",
        "frontend/src/main.ts",
        ".env.example",
        "frontend/.env.development",
        "backend/migrations/0005_t81_drop_chunks.sql",
        "backend/migrations/README.md",
        "backend/playground/schema_drift.py",
        "docker-compose.yml",
    ]
    bad2 = [p for p in must_keep if ignored(p)]
    check("G2 [反向] 源码/示例配置/白名单工具未被误拦", not bad2,
          f"共 {len(must_keep)} 项；误拦={bad2 or '无'}")

    # G3 正向：.gitignore 里管 docs/ 的规则还在（2026-09-17 起 docs/ 整体退出版本控制）。
    # 这里刻意带 --no-index：git check-ignore **默认会参考索引状态** —— 已追踪的文件
    # 不算「被忽略」（.gitignore 对已追踪文件本来就无效）。不带 --no-index 的话，
    # 这条会在「忘了 git rm --cached」时 FAIL，与 W4 完全重复。
    # 带上了，它就只问一件事：**规则本身还在不在**（规则被误删/改坏时立刻发现）。
    # 实测教训：这行的注释我第一版写反了（以为默认就不看索引），是靠 t83 跑出来的
    # 结果（G3 与 W4 同时 FAIL）才发现 —— 判据的语义别凭印象写。
    docs_must_ignore = ["docs/踩坑记录.md", "docs/PRD-知研-AI研究工作台.md"]
    bad3 = [
        p for p in docs_must_ignore
        if subprocess.run(["git", "check-ignore", "-q", "--no-index", p],
                          cwd=ROOT).returncode != 0
    ]
    check("G3 [正向] .gitignore 里 docs/ 的规则仍然有效", not bad3, f"漏网={bad3 or '无'}")


# ---------------------------------------------------------------- W 组
WHITELIST = [
    "backend/playground/schema_drift.py",
    "backend/playground/drift_check.py",
    "backend/playground/cleanup_orphans.py",
    "backend/playground/rebuild_vectors.py",
    "backend/playground/reload_vectors.py",
    "backend/playground/t82_real_env_test.py",
    "backend/playground/t82_storage_cleanup_test.py",
    "backend/playground/t86_probe_threshold_test.py",
    "backend/playground/t86_e2e_task_test.py",
    "backend/playground/t87_retriever_material_test.py",
    "backend/playground/t83_git_repo_check.py",
    "backend/playground/t87_langgraph_key_probe.py",
    "backend/playground/t88_critic_dimensions_test.py",
    "backend/playground/t88_frontend_dimensions_test.js",
    "backend/playground/t89a_has_material_test.py",
    "backend/playground/t89a_frontend_has_material_test.js",
    "backend/playground/t89b_precheck_test.py",
    "backend/playground/t89b_frontend_precheck_test.js",
    "backend/playground/t98_event_order_test.py",
    "backend/playground/check_worker_start.py",
    "backend/playground/t99_fixes_test.py",
    "backend/playground/t99_frontend_icon_test.js",
    "backend/playground/t100_compose_worker_test.py",
    "backend/playground/eval_retrieval.py",
]


# 根目录允许出现的顶层条目（文件 + 目录）。新增一项 = 明确表态「这东西要进公开仓」。
ROOT_WHITELIST = {
    ".gitattributes",
    ".gitignore",
    ".env.example",      # 模板，只有变量名和 change-me 之类的占位值（C1 保证没有 .env）
    "README.md",
    "docker-compose.yml",
    "docker-compose.prod.yml",   # 生产部署（2026-09-16 新增）
    "backend",
    "frontend",
    # docs 于 2026-09-17 移出（内部文档不公开）。它已不在仓库里，登记项也就不该留着 ——
    # 否则 W3b（正向：登记项一个不少）会一直 FAIL，而真正的原因是"清单没跟着改"。
}


def w_group(pending: list[str]) -> None:
    missing = [p for p in WHITELIST if p not in pending]
    check(f"W1 [白名单] {len(WHITELIST)} 个被引用的 playground 工具都进了仓库", not missing,
          f"缺={missing or '无'}")

    # W2 反向：未被文档点名的一次性探针**必须仍然被忽略**（否则白名单会变成"全收"）
    still_ignored = [
        "backend/playground/t78_epoch_test.py",
        "backend/playground/t81_drop_chunks_test.py",
        "backend/playground/t85_planner_kb_aware_test.py",
    ]
    leaked = [p for p in still_ignored if p in pending]
    check("W2 [反向] 未被点名的一次性探针仍被忽略", not leaked, f"漏放={leaked or '无'}")

    # W3 反向：根目录只允许登记过的条目 —— 拦住「清单外」的漏网（W1/W2 都是点名式判据，
    # 天然管不到从没被点过名的文件，_t60_fin.sh 就是这么溜进公开仓的）
    tops = sorted({p.split("/")[0] for p in pending})
    extra = [t for t in tops if t not in ROOT_WHITELIST]
    check(f"W3 [反向] 根目录无未登记条目（白名单 {len(ROOT_WHITELIST)} 项）",
          not extra, f"实际 {len(tops)} 项；多出={extra or '无'}")
    missing_top = [t for t in ROOT_WHITELIST if t not in tops]
    check("W3b [正向] 根目录登记项一个不少", not missing_top, f"缺={missing_top or '无'}")

    # W4 反向：当前树里不能有任何 docs/ 路径（2026-09-17 起内部文档整体退出版本控制）。
    # 与 G3 的分工：G3 查「规则有没有生效」，这条查「实际追踪状态」——
    # 加了 .gitignore 不会让**已追踪**的文件自动移出索引，万一漏了 git rm --cached，
    # G3 照样是绿的，只有这条能抓到。
    docs_tracked = [p for p in pending if p.startswith("docs/")]
    check("W4 [反向] 当前树里没有 docs/ 路径", not docs_tracked,
          f"命中={len(docs_tracked)}"
          + ("" if not docs_tracked
             else f"：{docs_tracked[:3]}{' …' if len(docs_tracked) > 3 else ''}"))


# ---------------------------------------------------------------- D 组
REF_RE = re.compile(r"playground/([A-Za-z0-9_][A-Za-z0-9_.-]*\.(?:py|md|sql|sh))")
# 文档里举例说明写法用的占位符，不是"叫你去跑某个文件"的引用（如 #85 的 `!backend/playground/xxx.py`）
PLACEHOLDER_RE = re.compile(r"(xxx|yyy|zzz|foo|bar|example|placeholder)", re.I)


def d_group(pending: list[str]) -> None:
    pending_set = set(pending)
    try:
        self_rel = Path(__file__).resolve().relative_to(ROOT).as_posix()
    except ValueError:
        self_rel = "backend/playground/t83_git_repo_check.py"
    dead: list[str] = []
    refs_found = 0
    scanned = 0

    for rel in pending:
        if rel == ".gitignore":
            continue          # 白名单规则本身写了这些文件名，不算"引用"
        if rel == self_rel:
            continue          # 自扫：本脚本的判据列表里写着一堆 playground 文件名
        p = ROOT / rel
        if not p.is_file():
            continue
        scanned += 1
        try:
            text = p.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        for m in REF_RE.finditer(text):
            if PLACEHOLDER_RE.search(m.group(1)):
                continue      # 文档里的示例占位符（`!backend/playground/xxx.py`），不是真引用
            name = "backend/playground/" + m.group(1)
            if name == rel:
                continue      # 自引用
            refs_found += 1
            if name not in pending_set:
                line = text[:m.start()].count("\n") + 1
                dead.append(f"{rel}:{line} → {name}")

    check("D1 [死引用] 入库文件引用的 playground 文件全部真实存在", not dead,
          f"扫 {scanned} 个入库文件、{refs_found} 处引用；死引用={len(dead)}" +
          ("" if not dead else "\n           " + "\n           ".join(sorted(set(dead)))))


# ---------------------------------------------------------------- C 组
# 硬编码凭据：把每行按**第一个 `=`** 切成 lhs / rhs —— 只要 **lhs 里出现敏感名**、
# 且 rhs 里有长度 ≥ 6 的引号字面量，就记为命中。
# 为什么这样切（2026-09-23 重写，两个方向各栽一次）：
#   ① 旧版是「敏感名 + `=` + **紧跟的一个**引号串」⇒ 遇到元组赋值
#      `EMAIL, PWD = "a@example.com", "realpass"`，抓到的永远是 `=` 后**第一个**值（邮箱），
#      真正的口令被逗号挡住、根本进不了捕获组；而那个邮箱恰好含 `example` ⇒ 又被占位符词表放过
#      ⇒ 判据报「命中=0」，6 个入库脚本里的明文口令一条都没抓到。
#   ② 改成「按行扫该行**所有**引号串」又太松：正常业务代码
#      `http.post('/auth/login', { email, password })` 同时有 `password` 和引号串 ⇒ 假红一片（32 处）。
#   ⇒ 折中是「只看赋值号**左边**的名字」：敏感名必须出现在**被赋值的变量**里，才可能是凭据。
# 两个刻意的收窄，都是为了不假红（判据假红几次就没人看了）：
#   ① 只认**带引号**的值 —— .env / .env.example 的 `JWT_SECRET=change-me` 是裸值，天然不命中；
#   ② 值命中占位符词表就放过 —— change-me / xxx / ${VAR} / *** 这类都不是真凭据；
#   ③ `pass` 必须带后缀（password/passwd/passphrase）—— 裸 `PASS = "..."` 是测试脚本里的
#      通过标记，不是凭据（`eval_retrieval.py:39` 就是这么被误报的）；
#   ④ 值本身就是「全大写的环境变量名」时放过 —— `PWD = os.environ.get("KP_TEST_PASSWORD")`
#      是**把口令挪出代码后的正确写法**，不收窄的话整改完反而会被判据报成新违规
#      （实测：6 个脚本整改完，C7 立刻报 6 条 `PWD（值长度 16）`，那 16 就是 `KP_TEST_PASSWORD`）。
#      代价：全大写字面量当口令时会漏检 —— 代码里罕见，可接受。
CRED_NAME_RE = re.compile(
    r"(?i)\b(pass(?:word|wd|phrase)|pwd|secret|api[_-]?key|access[_-]?token|auth[_-]?token)\b"
)
CRED_ASSIGN_RE = re.compile(r"^([^=]+)=(.*)$")           # 按第一个 `=` 切：左边是名字
CRED_VALUE_RE = re.compile(r"[\"']([^\"'\s]{6,})[\"']")   # 右边是候选值
CRED_COMMENT_RE = re.compile(r"^\s*(#|//|/\*|\*)")        # 注释/文档行整行跳过
# ⚠️ 不能并进 CRED_PLACEHOLDER_RE —— 那一条带 `(?i)`，加了 `[A-Z0-9_]+` 会大小写不敏感，
# 于是 `test123456` 这种纯字母数字的值全被放过（真口令一条都抓不到）。
CRED_ENVNAME_RE = re.compile(r"^[A-Z][A-Z0-9_]*$")        # 值本身就是「环境变量名」
CRED_PLACEHOLDER_RE = re.compile(
    r"(?i)change[-_]?me|placeholder|dummy|example|sample|xxx|yyy|your[-_]?|"
    r"<|\$\{|\{\{|[*]{3,}"
)
# 模板文件里本来就该写"变量名 + 示例值"，不是写死凭据
CRED_SCAN_EXCLUDE_RE = re.compile(r"(?i)\.(example|sample)$")
# 历史里不该出现的东西 —— 口径与 G1/C1/C2/C3 一致，只是把检查对象从「当前树」
# 换成「全部历史提交的树」。起因见 #103：文件删掉后当前树干净了，历史提交里还留着。
HIST_FORBIDDEN_RE = re.compile(r"^_|(^|/)\.env$|(^|/)(node_modules|\.venv)/|^backend/storage/")
# （2026-09-16：`t88/t89a/t89b` 三个前端实测脚本写死口令的债务已整改 ——
#  改成读 `KP_TEST_EMAIL` / `KP_TEST_PASSWORD` 环境变量，缺变量就报错退出。
#  原先那份 CRED_KNOWN_DEBT 显式例外清单随之删除，C7 恢复成**无例外**的纯判据。）


def c_group(pending: list[str]) -> None:
    secrets = [p for p in pending if re.search(r"(^|/)\.env$", p)]
    check("C1 [卫生] 仓库里没有任何 .env（密钥文件）", not secrets,
          f"命中={secrets or '无'}")

    dep_dirs = [p for p in pending if ".venv" in p or "node_modules" in p]
    check("C2 [卫生] 无依赖目录混入", not dep_dirs,
          f"命中={len(dep_dirs)} 条")

    runtime = [p for p in pending if p.startswith("backend/storage/")]
    check("C3 [卫生] storage/（用户上传文件）未入库", not runtime,
          f"命中={len(runtime)} 条")

    locks = [p for p in pending if p.endswith(("package-lock.json", "pnpm-lock.yaml", "yarn.lock"))]
    check("C4 [卫生] 前端只有一套锁文件", len(locks) == 1, f"命中={locks}")

    big = []
    for p in pending:
        fp = ROOT / p
        if fp.is_file() and fp.stat().st_size > 500_000:
            big.append(f"{p}({fp.stat().st_size // 1024}K)")
    check("C5 [卫生] 无超大文件（>500K）", not big, f"命中={big or '无'}")

    # 关键业务文件抽查
    key = [
        "backend/app/services/agent/nodes.py",
        "backend/app/services/agent/graph.py",
        "backend/app/services/agent/state.py",
        "backend/app/services/task_queue.py",
        "backend/app/services/rag/milvus_client.py",
        "backend/app/models/__init__.py",
        "backend/app/core/redis.py",
        "backend/app/core/storage.py",
        "backend/app/api/v1/endpoints/kb.py",
        "frontend/src/views/WorkbenchView.vue",
        "frontend/src/composables/useDocPolling.ts",
    ]
    miss = [p for p in key if p not in pending]
    check("C6 [完整] 关键业务文件一个不少", not miss, f"缺={miss or '无'}")

    # C7 内容兜底：不管文件叫什么名字、放在哪个目录，写死的口令都得被抓出来。
    # 口径见上面 CRED_NAME_RE 那段注释 —— 关键：**只看赋值号左边的名字**，值在 rhs 里找
    # （这样元组赋值 `a, PWD = "x", "y"` 的第二个值也能抓到，又不会把业务代码里的
    #  `{ email, password }` 参数当凭据）。
    hits: list[str] = []
    cred_scanned = 0
    for rel in pending:
        if CRED_SCAN_EXCLUDE_RE.search(rel):
            continue
        fp = ROOT / rel
        if not fp.is_file() or fp.stat().st_size > 500_000:
            continue
        try:
            text = fp.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        cred_scanned += 1
        for lineno, line in enumerate(text.splitlines(), 1):
            if CRED_COMMENT_RE.match(line):
                continue                                      # 注释里的提及不算写死凭据
            assign = CRED_ASSIGN_RE.match(line)
            if not assign:
                continue                                      # 没有 `=` ⇒ 不是赋值（类型声明/调用）
            name_m = CRED_NAME_RE.search(assign.group(1))     # 只在赋值号左边找敏感名
            if not name_m:
                continue
            for val_m in CRED_VALUE_RE.finditer(assign.group(2)):   # 再在右边找值
                val = val_m.group(1)
                if CRED_PLACEHOLDER_RE.search(val) or CRED_ENVNAME_RE.match(val):
                    continue          # 占位符 / 环境变量名，都不是写死的凭据（见上面 ②④）
                # ⚠️ 只报「位置 + 变量名 + 值长度」，**绝不打印值本身** ——
                # 判据的输出会进 CI 日志和终端历史，不能让它自己变成新的泄露面。
                hits.append(f"{rel}:{lineno} → {name_m.group(1)}（值长度 {len(val_m.group(1))}）")
    check("C7 [卫生] 源码/脚本里没有写死的口令", not hits,
          f"扫 {cred_scanned} 个文件；命中={len(hits)}"
          + ("" if not hits else "\n           " + "\n           ".join(sorted(set(hits)))))

    # C8 历史扫描：「删掉文件」不等于「从历史里删掉」。
    # 2026-09-16 的 _t60_*.sh 事故里，文件从当前树删除后 W3/C1~C7 全 PASS ——
    # 而它们还完整躺在 27 个历史提交里，且已经 push 到公开仓。
    # 这条把上面那些「查当前树」的口径延伸到全部历史提交。
    revs = git("rev-list", "--all").split()
    hist_hits: list[str] = []
    hist_docs: list[str] = []
    hist_paths = 0
    for c in revs:
        # -z 是必需的，不是风格问题：不带它时 git 会给**非 ASCII 文件名**加引号并做
        # 八进制转义（`"docs/\350\270\251\345\235\221..."`），于是 `startswith("docs/")`
        # 永远为假 —— 判据变成恒真的假 PASS。C9 第一次跑就是这么「全绿」的
        #（本仓库 docs/ 里 8 个文件全是中文名，一条都抓不到）。
        # C8 之前一直带着同一个盲区，只是 _t60_fin.sh 恰好是 ASCII 名才被抓到。
        for path in git("ls-tree", "-r", "-z", "--name-only", c).split("\0"):
            if not path:
                continue
            hist_paths += 1
            if HIST_FORBIDDEN_RE.search(path):
                hist_hits.append(f"{c[:7]}:{path}")
            if path.startswith("docs/"):
                hist_docs.append(f"{c[:7]}:{path}")
    shown = sorted(set(hist_hits))
    check(f"C8 [历史] {len(revs)} 个历史提交里都没有敏感文件", not shown,
          f"扫 {hist_paths} 条路径；命中={len(shown)}"
          + ("" if not shown else "\n           "
             + "\n           ".join(shown[:8])
             + (f"\n           …（共 {len(shown)} 条）" if len(shown) > 8 else "")))

    # C9 同样是历史口径，只是问的问题不同 —— 跟 C8 共用这次遍历（省一轮 git 调用）。
    # 「从当前树删掉」≠「从历史删掉」，这正是 C8 存在的理由，docs 同理。
    # 清理路径：filter-branch 剔除全部历史 + 远端删库重建 —— 重写只让旧提交「不可达」，
    # GitHub 服务端 GC 之前仍能按旧 SHA 直连读到内容，删库才是立刻生效的做法。
    shown_docs = sorted(set(hist_docs))
    check(f"C9 [历史] {len(revs)} 个历史提交里都没有 docs/", not shown_docs,
          f"扫 {hist_paths} 条路径；命中={len(shown_docs)}"
          + ("" if not shown_docs else "\n           "
             + "\n           ".join(shown_docs[:8])
             + (f"\n           …（共 {len(shown_docs)} 条）" if len(shown_docs) > 8 else "")))


def main() -> int:
    print(f"仓库根：{ROOT}")
    print(f"分支  ：{subprocess.run(['git','branch','--show-current'], cwd=ROOT, capture_output=True, text=True).stdout.strip()}")
    print()

    pending = tracked_or_pending()
    g_group()
    w_group(pending)
    d_group(pending)
    c_group(pending)

    print()
    print("-" * 78)
    passed = sum(1 for _, ok, _ in results if ok)
    print(f"结果：{passed}/{len(results)} PASS" + (f"，{len(skipped)} SKIP" if skipped else ""))
    for name, ok, d in results:
        if not ok:
            print(f"  FAIL {name}  {d}")
    print("-" * 78)
    return 0 if passed == len(results) else 1


if __name__ == "__main__":
    sys.exit(main())
