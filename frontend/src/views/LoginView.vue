<script setup lang="ts">
import { ref } from 'vue'
import { useRouter } from 'vue-router'
import { ElMessage } from 'element-plus'
import { useAuthStore } from '../stores/auth'

const version = import.meta.env.VITE_APP_VERSION

const router = useRouter()
const auth = useAuthStore()

const isLogin = ref(true)
const loading = ref(false)
const email = ref('')
const password = ref('')
const name = ref('')

async function submit() {
  if (!email.value || !password.value) {
    ElMessage.warning('请填写邮箱和密码')
    return
  }
  loading.value = true
  try {
    if (isLogin.value) {
      await auth.login(email.value, password.value)
    } else {
      await auth.register(email.value, password.value, name.value)
    }
    ElMessage.success(isLogin.value ? '登录成功' : '注册成功')
    router.push('/projects')
  } catch (e: any) {
    ElMessage.error(e?.response?.data?.detail || '操作失败')
  } finally {
    loading.value = false
  }
}
</script>

<template>
  <div class="login-wrap">
    <el-card class="login-card">
      <template #header>
        <div class="login-title">{{ isLogin ? '登录知研' : '注册知研' }}</div>
      </template>

      <el-form label-position="top">
        <el-form-item label="邮箱">
          <el-input v-model="email" placeholder="you@example.com" />
        </el-form-item>
        <el-form-item label="密码">
          <el-input v-model="password" type="password" show-password placeholder="密码" @keyup.enter="submit" />
        </el-form-item>
        <el-form-item v-if="!isLogin" label="昵称（可选）">
          <el-input v-model="name" placeholder="如何称呼你" />
        </el-form-item>

        <div class="login-actions">
          <el-button link type="primary" @click="isLogin = !isLogin">
            {{ isLogin ? '没有账号？去注册' : '已有账号？去登录' }}
          </el-button>
          <el-button type="primary" :loading="loading" @click="submit">
            {{ isLogin ? '登录' : '注册' }}
          </el-button>
        </div>
      </el-form>

      <div class="login-version">知研 KnowPilot {{ version }}</div>
    </el-card>
  </div>
</template>

<style scoped>
.login-wrap {
  height: 100vh;
  display: flex;
  align-items: center;
  justify-content: center;
}
.login-card {
  width: 380px;
}
.login-title {
  text-align: center;
  font-size: 18px;
  font-weight: 600;
}
.login-actions {
  display: flex;
  justify-content: space-between;
  align-items: center;
}
.login-version {
  text-align: center;
  margin-top: 16px;
  font-size: 13px;
  color: var(--el-text-color-secondary);
}
</style>
