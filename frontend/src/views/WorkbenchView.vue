<script setup lang="ts">
import { onBeforeUnmount, onMounted, ref } from "vue";
import { useRoute, useRouter } from "vue-router";
import { ElMessage, ElMessageBox } from "element-plus";
import { streamTaskEvents } from "../api/sse";
import { listKbs } from "@/api/kb";
import { taskList, createTask, precheckTask } from "@/api/tasks";
import type { IKb } from "@/typing/kb";
import type { IWork } from "@/typing/work";
import type { ITaskCreate } from "@/typing/tasks";

const route = useRoute();
const router = useRouter();
const projectId = route.params.id as string;

const objective = ref("");
const submitting = ref(false);
const busy = ref(false);
const taskId = ref<string | null>(null);
const logs = ref<Array<IWork>>([]);
const kbs = ref<Array<IKb>>([]);
const selectedKbs = ref<string[]>([]);
const lastReportId = ref<string | null>(null);

let stopStream: (() => void) | null = null;
let completedNotified = false;

function pushLog(text: string, type: string = "info") {
  logs.value.push({ time: new Date().toLocaleTimeString(), text, type });
}

async function submit() {
  if (objective.value.length < 5) {
    ElMessage.warning("调研目标至少 5 个字");
    return;
  }
  submitting.value = true;
  try {
    const payload: ITaskCreate = {
      project_id: projectId,
      objective: objective.value,
      sources: "all",
    };

    if (selectedKbs.value.length > 0) {
      payload.kb_ids = selectedKbs.value;
    }

    // 只在选了知识库时预检：没选 kb 就没有"资料够不够"这回事
    if (payload.kb_ids?.length) {
      const pre = await precheckTask({
        objective: payload.objective,
        kb_ids: payload.kb_ids,
      });
      if (pre.has_material === false) {
        try {
          await ElMessageBox.confirm(
            `所选知识库未检索到与「${payload.objective}」相关的资料（${pre.material_count} 条相关片段）。继续将仅凭模型知识撰写，报告可能缺少内部资料支撑。`,
            "未找到相关资料",
            {
              confirmButtonText: "仍要继续",
              cancelButtonText: "先去补资料",
              type: "warning",
            },
          );
        } catch (action) {
          if (action === "cancel") router.push("/knowledge-bases");
          return; // 取消按钮 / 右上角 X / ESC —— 都不发起
        }
      }
    }

    const task = await createTask(payload);
    taskId.value = task.id;
    busy.value = true;
    pushLog(`任务已发起（${task.id}），等待 Agent 执行…`);
    listen(task.id);
  } catch (e: any) {
    ElMessage.error(e?.response?.data?.detail || "发起失败");
  } finally {
    submitting.value = false;
  }
}

function gotoReport(id: string) {
  ElMessageBox.confirm("报告已生成，是否现在查看？", "调研完成", {
    confirmButtonText: "查看报告",
    cancelButtonText: "稍后查看",
    type: "success",
  })
    .then(() => {
      router.push(`/reports/${id}`);
    })
    .catch(() => {
      lastReportId.value = id;
    });
}

function onCompleted(reportId: string) {
  if (completedNotified) return;
  completedNotified = true;
  stopStream?.();
  pushLog("调研任务已完成", "success");
  busy.value = false;
  gotoReport(reportId);
}

function listen(id: string) {
  let lastStatus: string | null = null;
  stopStream = streamTaskEvents(id, {
    onEvent: (event, data) => {
      if (event === "agent_step") {
        const type = data.has_material === false ? "warning" : "primary";
        pushLog(`[${data.step}] ${data.detail || "…"}`, type);
      } else if (event === "task_status") {
        // 订阅时补发的状态快照：去重（SSE 重连会重复收到）+ completed 时带 report_id 直接跳转
        const st = data.status as string;
        if (st === lastStatus) return;
        lastStatus = st;
        if (st === "running") {
          pushLog("任务已在进行中（早期步骤可能已错过）", "warning");
        } else if (st === "completed") {
          if (data.report_id) onCompleted(data.report_id as string);
        } else if (st === "cancelled") {
          busy.value = false;
          pushLog("任务已取消", "warning");
        }
      } else if (event === "report_ready") {
        onCompleted(data.report_id as string);
      } else if (event === "task_failed") {
        busy.value = false;
        pushLog(`任务失败：${data.error}`, "danger");
      } else if (event === "task_cancelled") {
        busy.value = false;
        pushLog("任务已取消", "warning");
        stopStream?.();
      }
    },
    onError: () => pushLog("事件连接中断，正在重连…"),
  });
}

async function checkActive() {
  try {
    const tasks = await taskList();
    const active = tasks.find(
      (t) =>
        t.project_id === projectId &&
        (t.status === "pending" || t.status === "running"),
    );
    busy.value = !!active;
    if (active && !taskId.value) {
      taskId.value = active.id;
      pushLog(`接管进行中的任务（${active.id}），后续进度将实时更新`);
      listen(active.id);
    }
  } catch {
    busy.value = false;
  }
}

onMounted(async () => {
  try {
    checkActive();
    kbs.value = await listKbs();
  } catch {
    /* 知识库列表加载失败不阻塞发起 */
  }
});

onBeforeUnmount(() => stopStream?.());
</script>

<template>
  <div class="main">
    <div class="toolbar">
      <span class="project-id">项目 {{ projectId }}</span>
    </div>

    <div>
      <el-card class="form-card">
        <template #header>发起调研任务</template>
        <el-select
          v-model="selectedKbs"
          multiple
          clearable
          placeholder="选择知识库（可选，让 Agent 结合内部资料调研）"
          style="width: 100%; margin-bottom: 12px"
        >
          <el-option
            v-for="kb in kbs"
            :key="kb.id"
            :label="`${kb.name}（${kb.doc_count} 篇）`"
            :value="kb.id"
          />
        </el-select>
        <el-input
          v-model="objective"
          type="textarea"
          :rows="3"
          placeholder="例如：调研 2026 年中国 AI 智能体在客服领域的落地案例与市场规模，列出头部玩家对比"
        />
        <div class="form-actions">
          <el-button
            v-if="!busy"
            type="primary"
            :loading="submitting"
            :disabled="!objective"
            @click="submit"
          >
            开始调研
          </el-button>
          <el-alert
            v-else
            title="该项目有进行中任务，完成后可再次发起"
            type="warning"
            :closeable="false"
          />
        </div>
      </el-card>

      <el-card v-if="logs.length > 0" class="form-card">
        <template #header>Agent 执行过程</template>
        <el-timeline class="timeline">
          <el-timeline-item
            v-for="(log, i) in logs"
            :key="i"
            :type="log.type"
            :timestamp="log.time"
            :hollow="log.type === 'info'"
          >
            {{ log.text }}
            <el-button
              v-if="lastReportId && log.type === 'success'"
              type="primary"
              size="small"
              plain
              style="margin-left: 12px"
              @click="router.push(`/reports/${lastReportId}`)"
            >
              查看报告
            </el-button>
          </el-timeline-item>
        </el-timeline>
      </el-card>
      <el-empty
        v-else
        description="发起任务后，这里会实时显示 Agent 的思考与执行过程"
      />
    </div>
  </div>
</template>

<style scoped>
.main {
  max-width: 960px;
  margin: 0 auto;
  width: 100%;
}

.toolbar {
  display: flex;
  justify-content: space-between;
  align-items: center;
  margin-bottom: 16px;
}

.project-id {
  color: var(--el-text-color-secondary);
  font-size: 13px;
}

.main {
  max-width: 760px;
  margin: 0 auto;
  width: 100%;
}

.form-card {
  margin-bottom: 24px;
}

.form-actions {
  margin-top: 12px;
}

.log-line {
  display: flex;
  gap: 12px;
  padding: 4px 0;
  font-size: 13px;
}

.log-time {
  flex-shrink: 0;
  color: var(--el-text-color-secondary);
}
</style>
