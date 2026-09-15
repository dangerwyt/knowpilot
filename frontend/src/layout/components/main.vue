<template>
  <el-main class="layout-main">
    <el-scrollbar
      ref="layoutMainScrollbarRef"
      class="layout-main-scroll layout-backtop-header-fixed"
      wrap-class="layout-main-scroll"
      view-class="layout-main-scroll"
    >
      <router-view />
    </el-scrollbar>
    <!-- <el-backtop :target="setBacktopClass" /> -->
  </el-main>
</template>


<style scoped>
.layout-main {
  height: calc(100% - 51px);
}
</style>