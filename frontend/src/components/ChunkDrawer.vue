<script setup lang="ts">
import { ref, watch } from "vue";
import type { IKbDocDetail } from "@/typing/kb";
import { stripMarkdown } from "@/utils/format";

type DocDetail = Pick<IKbDocDetail, "chunks" | "file_name" | "chunk_count">;

const drawerVisible = ref(false);

const props = defineProps<{
  modelValue: boolean;
  docDetail: DocDetail;
  highlightSeq?: number | null;
}>();

const rowClass = ({ row }: { row: any }) =>
  props.highlightSeq != null && row.seq === props.highlightSeq
    ? "chunk-hit"
    : "";

watch(
  () => props.modelValue,
  (newVal) => {
    drawerVisible.value = newVal;
  },
);
</script>

<template>
  <el-drawer
    v-model="drawerVisible"
    direction="rtl"
    @close="$emit('update:modelValue', false)"
  >
    <template #header>
      <div class="drawer-title">文档分块 - {{ props.docDetail.file_name }}</div>
    </template>
    <div class="drawer-content">
      <el-table :data="props.docDetail.chunks" :row-class-name="rowClass">
        <el-table-column prop="seq" label="块ID" width="60" />
        <el-table-column label="块内容">
          <template #default="{ row }">
            <div class="chunk-content" :title="row.content">
              {{ stripMarkdown(row.content) }}
            </div>
          </template>
        </el-table-column>
      </el-table>
    </div>
  </el-drawer>
</template>

<style scoped>
.drawer-title {
  font-size: 16px;
  font-weight: bold;
}

.chunk-content {
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
  margin: 0;
}

:deep(.chunk-hit) {
  background-color: var(--el-color-primary-light-9);
}

:deep(.chunk-hit td) {
  background-color: var(--el-color-primary-light-9) !important;
}
</style>
