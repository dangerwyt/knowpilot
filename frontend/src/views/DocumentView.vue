<script setup lang="ts">
import { onMounted, ref, reactive } from "vue";
import type { IKb, IKbDoc, IKbDocDetail, IChunk } from "@/typing/kb";
import {
  listAllDocs,
  listKbs,
  deleteKbDoc,
  reparseKbDoc,
  getKbDocDetail,
} from "@/api/kb";
import {
  formatSize,
  formatDate,
  formatDocType,
  formatDocStatus,
} from "@/utils/format";
import { Filter, Refresh, View, Delete } from "@element-plus/icons-vue";
import { ElMessage, ElMessageBox } from "element-plus";
import ChunkDrawer from "@/components/ChunkDrawer.vue";
import { useDocPolling } from "@/composables/useDocPolling";

const { syncPending } = useDocPolling(()=>loadDocs(true));

type DocDetail = Pick<IKbDocDetail, "chunks" | "file_name" | "chunk_count">;

const loading = ref(false);

const drawerVisible = ref(false);

const kbId = ref("");

const kbs = reactive<IKb[]>([]);

const docs = reactive<IKbDoc[]>([]);

const docDetail = reactive<DocDetail>({
  chunks: [] as IChunk[],
  chunk_count: 0,
  file_name: "",
});

const loadDocDetail = async (docId: string) => {
  try {
    const res = await getKbDocDetail(docId);
    Object.assign(docDetail, res);
    drawerVisible.value = true;
  } catch (e: any) {
    ElMessage.error(e?.response?.data?.detail || "获取文档详情失败");
  }
};

async function deleteDoc(kbId: string, docId: string) {
  ElMessageBox.confirm("确认删除吗？", "删除确认", {
    confirmButtonText: "确定",
    cancelButtonText: "取消",
    type: "warning",
  }).then(async () => {
    try {
      await deleteKbDoc(kbId, docId);
      ElMessage.success("文档已删除");
      await load();
    } catch (e: any) {
      ElMessage.error(e?.response?.data?.detail || "删除失败");
    }
  });
}

async function reparseDoc(kbId: string, docId: string) {
  try {
    await reparseKbDoc(kbId, docId);
    ElMessage.success("文档已重新解析");
    await load();
  } catch (e: any) {
    ElMessage.error(e?.response?.data?.detail || "重新解析失败");
  }
}

const loadKbs = async () => {
  loading.value = true;
  kbs.length = 0;
  try {
    const res = await listKbs();
    kbs.push(...(res || []));
  } catch (error) {
    ElMessage.error("获取知识库列表失败");
  } finally {
    loading.value = false;
  }
};

const loadDocs = async (silent = false) => {
  if (!silent) loading.value = true;
  try {
    const res = await listAllDocs(kbId.value || "");
    docs.splice(0, docs.length, ...(res || []));
    syncPending(docs);
  } catch (error) {
    if (!silent) ElMessage.error("获取文档列表失败");
  } finally {
    if (!silent) loading.value = false;
  }
};


const handleDrawerClose = () => {
  drawerVisible.value = false;
  docDetail.chunks.length = 0;
  docDetail.chunk_count = 0;
  docDetail.file_name = "";
};

const load = async () => {
  await loadKbs();
  await loadDocs();
};

onMounted(load);
</script>

<template>
  <div class="doc-page">
    <div class="toolbar">
      <el-icon>
        <Filter />
      </el-icon>
      知识库
      <el-select
        class="kb-select"
        v-model="kbId"
        clearable
        placeholder="全部知识库"
        @change="load"
      >
        <el-option
          v-for="kb in kbs"
          :key="kb.id"
          :label="kb.name"
          :value="kb.id"
        />
      </el-select>
      <!-- <el-button @click="load">刷新</el-button> -->
    </div>
    <div class="doc-list">
      <el-table :data="docs">
        <el-table-column prop="file_name" label="文档名称" />
        <el-table-column label="类型">
          <template #default="{ row }">
            <el-tag :type="formatDocType(row.file_name).type"
              >{{ formatDocType(row.file_name).name }}
            </el-tag>
          </template>
        </el-table-column>
        <el-table-column label="状态">
          <template #default="{ row }">
            <el-tag :type="formatDocStatus(row.status).type">{{
              formatDocStatus(row.status).name
            }}</el-tag>
          </template>
        </el-table-column>
        <el-table-column prop="chunk_count" label="块数" />
        <el-table-column label="文件大小">
          <template #default="{ row }">
            {{ formatSize(row.size_bytes) }}
          </template>
        </el-table-column>
        <el-table-column label="创建时间">
          <template #default="{ row }">
            {{ formatDate(row.created_at) }}
          </template>
        </el-table-column>
        <el-table-column label="操作">
          <template #default="{ row }">
            <div class="action-icons">
              <el-tooltip content="查看分块">
                <el-icon
                  color="var(--el-color-primary)"
                  @click="loadDocDetail(row.id)"
                >
                  <View />
                </el-icon>
              </el-tooltip>
              <el-tooltip content="重新解析" v-if="row.status === 'failed'">
                <el-icon
                  color="var(--el-color-primary)"
                  @click="reparseDoc(row.kb_id, row.id)"
                >
                  <Refresh />
                </el-icon>
              </el-tooltip>
              <el-tooltip content="删除">
                <el-icon
                  color="var(--el-color-danger)"
                  @click="deleteDoc(row.kb_id, row.id)"
                >
                  <Delete />
                </el-icon>
              </el-tooltip>
            </div>
          </template>
        </el-table-column>
      </el-table>
    </div>
    <ChunkDrawer
      v-model="drawerVisible"
      :docDetail="docDetail"
      @update:modelValue="handleDrawerClose"
    />
  </div>
</template>

<style scoped>
.toolbar {
  display: flex;
  align-items: center;
  gap: 10px;
  font-size: 13px;
}

.kb-select {
  width: 180px;
}

.doc-list {
  margin-top: 20px;
}

.action-icons {
  display: flex;
  font-size: 14px;
  gap: 8px;
}

.action-icons > .el-icon {
  cursor: pointer;
}
</style>
