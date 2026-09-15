<script setup lang="ts">
import { onMounted, ref, reactive, computed } from "vue";
import { useRoute, useRouter } from "vue-router";
import { ElMessage } from "element-plus";
import { marked } from "marked";
import DOMPurify from "dompurify";
import { getReports, getReportsCitations, exportReport } from "@/api/reports";
import { getKbDocDetail } from "@/api/kb";
import { getTask, createTask } from "@/api/tasks";
import ChunkDrawer from "@/components/ChunkDrawer.vue";

const route = useRoute();
const router = useRouter();
const reportId = route.params.id as string;

const report = ref<any>(null);
const citations = ref<any>(null);
const issuesVisible = ref<boolean>(false);
const exporting = ref<boolean>(false);
const rerunning = ref<boolean>(false);
const rewriteVisible = ref(false);
const rewriteInput = ref("");
const rewriting = ref(false);
const sourceDrawer = ref(false);
const sourceDetail = reactive({ chunks: [], file_name: "", chunk_count: 0 });
const sourceLoading = ref(false);
const matchedSeq = ref<number | null>(null);

const renderMarkdown = (text: string) =>
  DOMPurify.sanitize(marked.parse(text) as string);

const quality = computed(() => report.value?.content?.quality);

const groupedCitations = computed(() => {
  const map = new Map<string, any>();
  for (const c of citations.value || []) {
    // 按 document_id 分组（重名文档不会被合并）；document_id 为 NULL 的走退化键
    const key = c.document_id || `__no_doc__${c.source_title}`;
    if (!map.has(key)) {
      map.set(key, {
        ...c,
        key,
        sections: [c.section_id],
        snippets: c.snippet ? [c.snippet] : [],
      });
    } else {
      const ex = map.get(key);
      if (!ex.sections.includes(c.section_id)) ex.sections.push(c.section_id);
      if (c.snippet && !ex.snippets.includes(c.snippet)) ex.snippets.push(c.snippet);
    }
  }
  return [...map.values()];
});

const getReport = async () => {
  try {
    report.value = await getReports(reportId);
  } catch (e: any) {
    ElMessage.error(e?.response?.data?.detail || "报告加载失败");
  }
};

const getCitations = async () => {
  try {
    citations.value = await getReportsCitations(reportId);
  } catch (e: any) {
    ElMessage.error(e?.response?.data?.detail || "引用加载失败");
  }
};

const openSource = async (item: any) => {
  if (!item.document_id) {
    ElMessage.info("该来源已不可用（文档已删除或为外部链接）");
    return;
  }
  sourceDetail.file_name = item.source_title;
  sourceLoading.value = true;
  sourceDrawer.value = true;
  try {
    const res: any = await getKbDocDetail(item.document_id);
    Object.assign(sourceDetail, res);
    // 用「包含」而不是「相等」定位：snippet 是检索块的前 200 字截断，比库里那块短
    const probe = (item.snippets?.[0] || "").slice(0, 20);
    const hit = (res.chunks || []).find((c: any) =>
      (c.content || "").includes(probe)
    );
    matchedSeq.value = hit ? hit.seq : null;
  } catch (e: any) {
    ElMessage.error(e?.response?.data?.detail || "原文加载失败");
  } finally {
    sourceLoading.value = false;
  }
};

const handleExportReport = async () => {
  exporting.value = true;
  try {
    const blob = await exportReport(reportId);
    const url = URL.createObjectURL(blob);
    const a = document.createElement("a");
    a.href = url;
    a.download = `${report.value.title || "report"}.md`;
    document.body.appendChild(a);
    a.click();
    a.remove();
    URL.revokeObjectURL(url);
  } catch (e: any) {
    ElMessage.error(e?.response?.data?.detail || "报告导出失败");
  } finally {
    exporting.value = false;
  }
};

const handleRerun = async () => {
  rerunning.value = true;
  try {
    const task = await getTask(report.value.task_id);
    await createTask({
      project_id: task.project_id,
      objective: task.objective,
      kb_ids: task.kb_ids || undefined,
      sources: "all",
    });
    ElMessage.success("已发起新的调研任务");
    router.push(`/projects/${task.project_id}`);
  } catch (e: any) {
    ElMessage.error(e?.response?.data?.detail || "发起失败");
  } finally {
    rerunning.value = false;
  }
};

const handleRewrite = async () => {
  const fb = rewriteInput.value.trim();
  if (fb.length < 5) {
    ElMessage.warning("请至少写 5 个字，说清希望怎么改");
    return;
  }
  rewriting.value = true;
  try {
    const task = await getTask(report.value.task_id);
    // 意见拼进 objective：原目标 + 分隔标记 + 本次修改要求
    // 叠加是有意的：第二次带意见重写时，模型能看到历史意见链
    const merged = `${task.objective}\n\n【本次修改要求】\n${fb}`;
    await createTask({
      project_id: task.project_id,
      objective: merged,
      kb_ids: task.kb_ids || undefined,
      sources: "all",
    });
    rewriteVisible.value = false;
    rewriteInput.value = "";
    ElMessage.success("已带意见重新发起调研");
    router.push(`/projects/${task.project_id}`);
  } catch (e: any) {
    ElMessage.error(e?.response?.data?.detail || "发起失败");
  } finally {
    rewriting.value = false;
  }
};

onMounted(() => {
  getReport();
  getCitations();
});
</script>

<template>
  <div class="report-page">
    <el-card v-if="report" class="report-card">
      <template #header>
        <div class="report-header">
          <span class="report-title">{{ report.title }}</span>
          <div class="report-quality">
            <el-button
              v-if="quality?.passed === false"
              size="small"
              type="warning"
              :loading="rewriting"
              @click="rewriteVisible = true"
            >
              带意见重写
            </el-button>
            <el-button size="small" :loading="rerunning" @click="handleRerun">重新调研</el-button>
            <el-button size="small" :loading="exporting" @click="handleExportReport">导出报告</el-button>
            <el-tag v-if="quality" :type="quality.passed === true
              ? 'success'
              : quality.passed === false
                ? 'warning'
                : 'info'
              " size="small" effect="light">
              {{
                quality.passed === true
                  ? `✅ 质检通过（${quality.score}分）`
                  : quality.passed === false
                    ? `⚠️ 质检未达标（${quality.score}分）`
                    : "未执行质检"
              }}
            </el-tag>
            <el-button v-if="quality?.issues?.length" link type="primary" size="small" @click="issuesVisible = true">
              评审意见（{{ quality.issues.length }}）
            </el-button>
          </div>
        </div>
      </template>
      <template v-for="item in report.content.sections" :key="item.title">
        <h2 class="section-title">{{ item.title }}</h2>
        <div class="section-content" v-html="renderMarkdown(item.content)" />
      </template>
      <div class="report-version">版本 v{{ report.version }}</div>
    </el-card>
    <div v-else class="loading-wrap">
      <el-skeleton :rows="6" animated />
    </div>

    <el-card v-if="groupedCitations.length > 0" class="report-card">
      <template #header>引用来源</template>
      <div v-for="item in groupedCitations" :key="item.key" class="citation-item"
        :class="{ 'is-clickable': item.document_id }" @click="openSource(item)">
        <div class="citation-head">
          <el-tag size="small" :type="item.source_type === 'kb' ? 'success' : 'info'">
            {{ item.source_type === "kb" ? "知识库" : item.source_type }}
          </el-tag>
          <span class="citation-title">{{ item.source_title }}</span>
          <!-- 显示被哪些章节引用过 -->
          <span class="citation-sections">被 {{ item.sections.length }} 章引用</span>
        </div>
        <p v-if="item.snippets?.length" class="citation-snippet">
          “{{ item.snippets.join(" … ") }}”
        </p>
      </div>
    </el-card>
    <el-dialog v-model="issuesVisible" title="质检评审意见" width="560px">
      <ol class="issue-list">
        <li v-for="(issue, i) in quality.issues" :key="i">{{ issue }}</li>
      </ol>
    </el-dialog>
    <el-dialog v-model="rewriteVisible" title="带意见重写" width="620px">
      <el-alert
        v-if="quality?.issues?.length"
        type="info"
        :closable="false"
        style="margin-bottom: 12px"
      >
        <template #title>
          质检发现 {{ quality.issues.length }} 个问题，可在「评审意见」里查看后，挑你要改的写进下方
        </template>
      </el-alert>
      <el-input
        v-model="rewriteInput"
        type="textarea"
        :rows="6"
        placeholder="例如：补充一节「结论」；技术架构章节补上资料来源编号；第一、三章内容重复，请合并论述"
      />
      <div class="rewrite-tip">
        注意：这会以「原调研目标 + 你的意见」发起一次全新的调研，旧报告会完整保留。
      </div>
      <template #footer>
        <el-button @click="rewriteVisible = false">取消</el-button>
        <el-button
          type="primary"
          :loading="rewriting"
          :disabled="rewriteInput.trim().length < 5"
          @click="handleRewrite"
        >
          提交并重新调研
        </el-button>
      </template>
    </el-dialog>
    <ChunkDrawer v-model="sourceDrawer" :doc-detail="sourceDetail" :highlight-seq="matchedSeq" />
  </div>
</template>

<style scoped>
.report-page {
  max-width: 860px;
  margin: 0 auto;
  padding: 24px;
}

.report-card {
  margin-bottom: 24px;
}

.report-header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 12px;
}

.report-title {
  font-size: 20px;
  font-weight: 600;
}

.report-quality {
  display: flex;
  gap: 12px;
}

.section-title {
  font-size: 18px;
  margin: 20px 0 8px;
}

.section-content :deep(h1),
.section-content :deep(h2),
.section-content :deep(h3) {
  margin: 12px 0 8px;
  font-size: 1.15em;
}

.section-content :deep(p) {
  margin: 8px 0;
  line-height: 1.8;
}

.section-content :deep(ul),
.section-content :deep(ol) {
  padding-left: 20px;
}

.section-content :deep(code) {
  background: var(--el-fill-color-light);
  padding: 2px 6px;
  border-radius: 4px;
  font-size: 0.9em;
}

.report-version {
  font-size: 13px;
  color: var(--el-text-color-secondary);
  margin-top: 16px;
}

.loading-wrap {
  padding: 24px;
}

.empty-tip {
  color: var(--el-text-color-secondary);
  font-size: 13px;
}

.citation-item {
  margin-bottom: 12px;
}

.citation-head {
  display: flex;
  gap: 8px;
  align-items: center;
}

.citation-title {
  font-weight: 500;
}

.citation-snippet {
  color: var(--el-text-color-secondary);
  font-size: 13px;
  margin: 4px 0 0;
  padding-left: 12px;
  border-left: 2px solid var(--el-border-color);
}

.issue-list li {
  margin: 8px 0;
  color: var(--el-text-color-secondary);
  line-height: 1.7;
  text-align: left;
}

.rewrite-tip {
  margin-top: 10px;
  font-size: 12px;
  color: var(--el-text-color-secondary);
  line-height: 1.6;
}

.citation-item.is-clickable {
  cursor: pointer;
  border-radius: 6px;
  transition: background-color 0.2s;
}
.citation-item.is-clickable:hover {
  background-color: var(--el-fill-color-light);
}
</style>
