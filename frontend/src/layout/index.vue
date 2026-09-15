<script setup lang="ts">
import { defineAsyncComponent } from 'vue';

const LayoutHeader = defineAsyncComponent(() => import('@/layout/components/header.vue'));
const LayoutMain = defineAsyncComponent(() => import('@/layout/components/main.vue'));
</script>

<template>
    <el-container class="layout-container layout-backtop" direction="vertical">
        <LayoutHeader />
        <LayoutMain ref="layoutMainRef" />
    </el-container>
</template>

<style scoped>
.layout-container {
    height: 100%;
}
</style>
