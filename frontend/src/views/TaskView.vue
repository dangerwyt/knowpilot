<script setup lang="ts">
import { onMounted, ref } from "vue";
import { useRouter } from "vue-router";
import { taskList, resumeTask, cancelTask } from "@/api/tasks";
import { TASK_STATUS_MAP } from "@/utils/constant";
import { formatDate } from "@/utils/format";
import type { ITask } from "@/typing/tasks";
import { ElMessage, ElMessageBox } from "element-plus";

const router = useRouter();

const data = ref<ITask[]>([]);
const loading = ref(false);

function statusType(status: string) {
  if (!TASK_STATUS_MAP.has(status)) {
    return {
      type: "info",
      name: "未知状态",
    };
  } else {
    return {
      type: TASK_STATUS_MAP.get(status)?.type,
      name: TASK_STATUS_MAP.get(status)?.name,
    };
  }
}

const play = async (row: ITask) => {
  if (row.status === "failed") {
    ElMessage.error("生成报告失败，请稍后重试");
    return;
  }
  if (row.report_id) {
    router.push(`/reports/${row.report_id}`);
  } else {
    ElMessage.info("报告生成中，请稍候刷新");
  }
}

const resume = async (row: ITask) => {
  try {
    await resumeTask(row.id);
    ElMessage.success("任务已恢复执行，可稍候刷新查看进度");
    getTasks();
  } catch (e: any) {
    ElMessage.error(e?.response?.data?.detail || "恢复失败");
  }
};

const cancel = (row: ITask) => {
  ElMessageBox.confirm("正在执行的步骤会跑完，之后不再继续执行，确认取消任务吗？", "确认取消", {
    confirmButtonText: "确定",
    cancelButtonText: "取消",
    type: "warning",
  }).then(async () => {
    try {
      await cancelTask(row.id);
      ElMessage.success("任务已取消");
      getTasks();
    } catch (e: any) {
      ElMessage.error(e?.response?.data?.detail || "取消失败");
    }
  });
}

const getTasks = async () => {
  loading.value = true;
  try {
    data.value = await taskList();
  } catch (e: any) {
    ElMessage.error(e?.response?.data?.detail || "加载失败");
  } finally {
    loading.value = false;
  }
};

onMounted(getTasks);
</script>

<template>
  <div class="tasks-page">
    <h3 class="page-title">任务列表</h3>
    <el-table :data="data" v-loading="loading" stripe>
      <el-table-column
        prop="objective"
        label="调研目标"
        show-overflow-tooltip
      />
      <el-table-column label="状态" width="110">
        <template #default="{ row }">
          <el-tag :type="statusType(row.status).type" size="small">
            {{ statusType(row.status).name }}
          </el-tag>
        </template>
      </el-table-column>
      <el-table-column prop="score" label="分数" width="80" />
      <el-table-column prop="passed" label="质检" width="110">
        <template #default="{ row }">
          <el-tag
            v-if="row.status === 'completed'"
            :type="row.passed ? 'success' : 'warning'"
            size="small"
          >
            {{ row.passed ? "合格" : "不合格" }}
          </el-tag>
        </template>
      </el-table-column>
      <el-table-column label="耗时" width="100">
        <template #default="{ row }"> {{ row.elapsed_seconds }} 秒 </template>
      </el-table-column>
      <el-table-column prop="created_at" label="创建时间" width="200">
        <template #default="{ row }">
          {{ formatDate(row.created_at) }}
        </template>
      </el-table-column>
      <el-table-column label="操作" width="110">
        <template #default="{ row }">
          <el-button
            v-if="row.status === 'running' || row.status === 'pending'"
            link
            type="danger"
            @click="cancel(row)"
            >取消
          </el-button>
          <el-button
            v-else-if="row.status === 'failed'"
            link
            type="danger"
            @click="play(row)"
          >
            失败
          </el-button>
          <el-button
            v-else-if="row.status === 'cancelled'"
            link
            type="warning"
          >
            已取消
          </el-button>
          <el-button
            v-else-if="row.status === 'interrupted'"
            link
            type="warning"
            @click="resume(row)"
            >恢复
          </el-button>
          <el-button v-else link type="primary" @click="play(row)"
            >查看报告</el-button
          >
        </template>
      </el-table-column>
    </el-table>
  </div>
</template>

<style scoped>
.page-title {
  margin: 0 0 16px;
}
</style>
