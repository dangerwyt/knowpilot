<script setup lang="ts">
import { onMounted, ref, reactive } from "vue";
import { ElMessage, ElMessageBox } from "element-plus";
import ChunkDrawer from "@/components/ChunkDrawer.vue";
import {
  Refresh,
  View,
  Delete,
  Document,
  Upload,
} from "@element-plus/icons-vue";
import type { IKb, IKbDoc, IKbDocDetail, IChunk } from "@/typing/kb";
import {
  formatSize,
  formatDate,
  formatDocType,
  formatDocStatus,
} from "@/utils/format";
import {
  createKb,
  listKbs,
  deleteKb,
  listKbsDocs,
  deleteKbDoc,
  uploadKbDoc,
  reparseKbDoc,
  getKbDocDetail,
} from "@/api/kb";
import { useDocPolling } from "@/composables/useDocPolling";

const { syncPending, stop: stopPolling } = useDocPolling(() => refreshDocs());

const kbs = ref<IKb[]>([]);
const showCreate = ref(false);
const showDoc = ref(false);
const showChunkDrawer = ref(false);
const newName = ref("");
const docs = ref<IKbDoc[]>([]);
const currentKb = ref("");

const docDetail = reactive<DocDetail>({
  chunks: [] as IChunk[],
  chunk_count: 0,
  file_name: "",
});

type DocDetail = Pick<IKbDocDetail, "chunks" | "file_name" | "chunk_count">;

async function load() {
  kbs.value = await listKbs();
}

async function create() {
  if (!newName.value) return;
  await createKb(newName.value);
  showCreate.value = false;
  newName.value = "";
  ElMessage.success("知识库已创建");
  await load();
}

function removeKb(kbId: string) {
  ElMessageBox.confirm("将删除该知识库及其全部文档，确认删除吗？", "删除确认", {
    confirmButtonText: "确定",
    cancelButtonText: "取消",
    type: "warning",
  }).then(async () => {
    try {
      await deleteKb(kbId);
      ElMessage.success("知识库已删除");
      await load();
    } catch (e: any) {
      ElMessage.error(e?.response?.data?.detail || "删除失败");
    }
  });
}

async function loadDocs(kbId: string) {
  showDoc.value = true;
  currentKb.value = kbId;
  try {
    docs.value = await listKbsDocs(kbId);
    syncPending(docs.value);
  } catch (e: any) {
    ElMessage.error(e?.response?.data?.detail || "加载失败");
  }
}

async function uploadFile(opt: any) {
  const form = new FormData();
  form.append("file", opt.file);
  try {
    await uploadKbDoc(currentKb.value, form);
    ElMessage.success("上传成功，正在解析…");
    loadDocs(currentKb.value);
  } catch (e: any) {
    ElMessage.error(e?.response?.data?.detail || "上传失败");
  }
}

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
      loadDocs(kbId);
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
    loadDocs(kbId);
  } catch (e: any) {
    ElMessage.error(e?.response?.data?.detail || "重新解析失败");
  }
}

const refreshDocs = async () => {
  if (!currentKb.value) return;
  try {
    docs.value = await listKbsDocs(currentKb.value);
    syncPending(docs.value);
  } catch {
    // 轮询静默
  }
}

const loadDocDetail = async (docId: string) => {
  try {
    const res = await getKbDocDetail(docId);
    Object.assign(docDetail, res);
    showChunkDrawer.value = true;
  } catch (e: any) {
    ElMessage.error(e?.response?.data?.detail || "获取文档详情失败");
  }
};

const handleDrawerClose = () => {
  showChunkDrawer.value = false;
  docDetail.chunks.length = 0;
  docDetail.chunk_count = 0;
  docDetail.file_name = "";
};

onMounted(async () => {
  try {
    await load();
  } catch (e: any) {
    ElMessage.error(e?.response?.data?.detail || "加载失败");
  }
});
</script>

<template>
  <div class="kb-page">
    <div class="toolbar">
      <h3 style="margin: 0">知识库</h3>
      <el-button @click="showCreate = true">新建知识库</el-button>
    </div>

    <el-empty v-if="kbs.length === 0" description="还没有知识库。上传团队文档后，Agent 可结合内部资料调研" />

    <el-table :data="kbs" style="width: 100%">
      <el-table-column prop="name" label="知识库名称" />
      <el-table-column label="文档数">
        <template #default="{ row }">
          <el-tag type="primary">{{ row.doc_count }}</el-tag>
        </template>
      </el-table-column>
      <el-table-column prop="created_at" label="创建时间">
        <template #default="{ row }">
          {{ formatDate(row.created_at) }}
        </template>
      </el-table-column>
      <el-table-column label="操作" width="110">
        <template #default="{ row }">
          <div class="action-icons">
            <el-tooltip content="文档管理">
              <el-icon color="var(--el-color-primary)" @click="loadDocs(row.id)">
                <Document />
              </el-icon>
            </el-tooltip>

            <el-tooltip content="删除知识库">
              <el-icon color="var(--el-color-danger)" @click="removeKb(row.id)">
                <Delete />
              </el-icon>
            </el-tooltip>
          </div>
        </template>
      </el-table-column>
    </el-table>

    <el-dialog v-model="showCreate" title="新建知识库" width="420px">
      <el-input v-model="newName" placeholder="知识库名称" />
      <template #footer>
        <el-button type="primary" @click="create">创建</el-button>
      </template>
    </el-dialog>

    <el-dialog v-model="showDoc" title="文档管理" width="1000px" @close="stopPolling">
      <el-upload :show-file-list="false" :http-request="uploadFile" accept=".md,.txt,.pdf,.docx"
        style="margin-bottom: 12px">
        <el-button type="primary" size="small" :icon="Upload">上传文档（md/txt/pdf/docx）</el-button>
      </el-upload>
      <el-table :data="docs">
        <el-table-column prop="file_name" label="文档名称" />
        <el-table-column label="类型" width="120">
          <template #default="{ row }">
            <el-tag :type="formatDocType(row.file_name).type">{{ formatDocType(row.file_name).name }}
            </el-tag>
          </template>
        </el-table-column>
        <el-table-column label="状态" width="120">
          <template #default="{ row }">
            <el-tag :type="formatDocStatus(row.status).type">{{
              formatDocStatus(row.status).name
            }}</el-tag>
          </template>
        </el-table-column>
        <el-table-column prop="chunk_count" label="块数" width="120" />
        <el-table-column label="文件大小" width="120">
          <template #default="{ row }">
            {{ formatSize(row.size_bytes) }}
          </template>
        </el-table-column>
        <el-table-column label="创建时间">
          <template #default="{ row }">
            {{ formatDate(row.created_at) }}
          </template>
        </el-table-column>
        <el-table-column label="操作" width="100">
          <template #default="{ row }">
            <div class="action-icons">
              <el-tooltip content="查看分块">
                <el-icon color="var(--el-color-primary)" @click="loadDocDetail(row.id)">
                  <View />
                </el-icon>
              </el-tooltip>
              <el-tooltip content="重新解析" v-if="row.status === 'failed'">
                <el-icon color="var(--el-color-primary)" @click="reparseDoc(row.kb_id, row.id)">
                  <Refresh />
                </el-icon>
              </el-tooltip>
              <el-tooltip content="删除">
                <el-icon color="var(--el-color-danger)" @click="deleteDoc(row.kb_id, row.id)">
                  <Delete />
                </el-icon>
              </el-tooltip>
            </div>
          </template>
        </el-table-column>
      </el-table>
    </el-dialog>
    <ChunkDrawer v-model="showChunkDrawer" :docDetail="docDetail" @update:modelValue="handleDrawerClose" />
  </div>
</template>

<style scoped>
.toolbar {
  display: flex;
  justify-content: space-between;
  align-items: center;
  margin-bottom: 16px;
}

.action-icons {
  display: flex;
  font-size: 14px;
  gap: 8px;
}

.action-icons>.el-icon {
  cursor: pointer;
}
</style>
