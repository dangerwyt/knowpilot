<script setup lang="ts">
import { computed } from "vue";
import { useRouter } from "vue-router";
import { useAuthStore } from "@/stores/auth";

const router = useRouter();
const authStore = useAuthStore();

const firstLetter = computed(() => {
  const letter = authStore.user?.name?.charAt(0) || " ";
  return letter.toUpperCase();
});

const logout = () => {
  authStore.logout();
  router.push("/login");
};
</script>

<template>
  <el-dropdown placement="bottom">
    <div class="user-btn">
      <el-avatar :size="30">
        {{ firstLetter }}
      </el-avatar>
      &nbsp;
      {{ authStore.user?.name || "用户" }}
    </div>
    <template #dropdown>
      <el-dropdown-menu>
        <el-dropdown-item @click="logout">退出登录</el-dropdown-item>
      </el-dropdown-menu>
    </template>
  </el-dropdown>
</template>
