<script setup lang="ts" name="layoutHeader">
import { defineAsyncComponent } from "vue";

const Logo = defineAsyncComponent(() => import("@/layout/components/logo.vue"));
const Menu = defineAsyncComponent(() => import("@/layout/components/menu.vue"));
const User = defineAsyncComponent(() => import("@/layout/components/user.vue"));
</script>

<template>
  <el-header class="layout-header">
    <div class="layout-navbars">
      <div class="left">
        <Logo />
        <Menu />
      </div>
      <div class="right">
        <User />
      </div>
    </div>
  </el-header>
</template>

<style scoped>
.layout-header {
  border-bottom: 1px solid #e4e7ed;
}
.layout-navbars {
  width: 100%;
  height: 100%;
  display: flex;
  align-items: center;
  justify-content: space-between;
}
.layout-navbars .left {
  display: flex;
  align-items: center;
}
.layout-navbars .right {
  display: flex;
  align-items: center;
  cursor: pointer;
  margin-right: 30px;
}
:deep(.el-dropdown *) {
  outline: none !important;
}
</style>
