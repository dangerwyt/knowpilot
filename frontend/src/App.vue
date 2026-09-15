<script setup lang="ts">
import { ref } from 'vue'
import { health } from '@/api/other'

const state = ref(false)

const isHealth = () => {
  health('/health')
    .then(() => {
      state.value = true
    })
    .catch(() => {
      state.value = false
    })
}

isHealth()
</script>

<template>
  <!-- <div class="bar" :class="state ? 'online' : 'offline'">{{ state ? '已连接' : '离线' }}</div> -->
  <router-view />
</template>

<style>
html,
body,
#app {
  height: 100%;
  margin: 0;
}
.bar {
  padding: 4px 12px;
  font-size: 13px;
  color: #fff;
  text-align: center;
}
.bar.online {
  background-color: #18a058;
}
.bar.offline {
  background-color: #d03050;
}
</style>
