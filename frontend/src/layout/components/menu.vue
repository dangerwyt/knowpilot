<script setup lang="ts" name="layoutMenu">
import { reactive, computed, watch } from "vue";
import { useRouter, useRoute } from "vue-router";

const route = useRoute();
const routes = useRouter().getRoutes();
const menuLists = computed(() => routes.filter((item) => !item.meta?.hidden));

const state = reactive({
  defaultActive: "",
});

watch(
  () => route.path,
  (newPath) => {
    state.defaultActive = newPath;
  },
  { immediate: true },
);
</script>
<template>
  <el-menu
    class="menu-wrapper"
    router
    :default-active="state.defaultActive"
    :ellipsis="false"
    background-color="transparent"
    mode="horizontal"
  >
    <template v-for="menu in menuLists">
      <el-menu-item :index="menu.path">
        <template #title>
          {{ menu?.meta?.title }}
        </template>
      </el-menu-item>
    </template>
  </el-menu>
</template>

<style scoped>
.menu-wrapper {
  border-bottom: none;
  border-right: none;
}
</style>
