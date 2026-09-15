<script setup lang="ts">
import { onMounted, ref } from 'vue'
import { useRouter } from 'vue-router'
import { ElMessage } from 'element-plus'
import http from '../api/http'

const router = useRouter()

interface Project {
  id: string
  name: string
  description?: string
  created_at: string
}

const projects = ref<Project[]>([])
const loading = ref(false)
const showCreate = ref(false)
const newName = ref('')
const newDesc = ref('')

async function load() {
  loading.value = true
  try {
    projects.value = await http.get<Project[]>('/projects')
  } catch (e: any) {
    ElMessage.error(e?.response?.data?.detail || '加载失败')
  } finally {
    loading.value = false
  }
}

async function create() {
  if (!newName.value) return
  await http.post<Project>('/projects', { name: newName.value, description: newDesc.value })
  showCreate.value = false
  newName.value = ''
  newDesc.value = ''
  ElMessage.success('项目已创建')
  await load()
}

onMounted(load)
</script>

<template>
  <el-container>
    <el-main class="main">
      <div class="toolbar">
        <h3 style="margin: 0">调研项目</h3>
        <el-button type="primary" @click="showCreate = true">新建项目</el-button>
      </div>

      <el-empty v-if="!loading && projects.length === 0" description="还没有项目，创建一个开始第一次调研" />

      <el-row :gutter="16">
        <el-col v-for="p in projects" :key="p.id" :xs="24" :sm="12">
          <el-card shadow="hover" class="project-card">
            <div class="project-head">
              <span class="project-name">{{ p.name }}</span>
              <el-button type="primary" size="small" @click.stop="router.push(`/projects/${p.id}`)">
                查看
              </el-button>
            </div>
            <div class="project-desc">{{ p.description || '暂无描述' }}</div>
          </el-card>
        </el-col>
      </el-row>

      <el-dialog v-model="showCreate" title="新建项目" width="480px">
        <el-input v-model="newName" placeholder="项目名称" style="margin-bottom: 12px" />
        <el-input v-model="newDesc" type="textarea" :rows="3" placeholder="项目描述（可选）" />
        <template #footer>
          <el-button type="primary" @click="create">创建</el-button>
        </template>
      </el-dialog>
    </el-main>
  </el-container>
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
.project-card {
  margin-bottom: 16px;
}
.project-head {
  display: flex;
  justify-content: space-between;
  align-items: center;
}
.project-name {
  font-weight: 600;
}
.project-desc {
  color: var(--el-text-color-secondary);
  font-size: 13px;
  margin-top: 4px;
}
</style>
