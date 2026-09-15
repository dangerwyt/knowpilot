<script setup lang="ts" name="layoutLogo">
const APP_NAME = import.meta.env.VITE_APP_NAME
</script>

<template>
	<div class="layout-logo">
		<span>{{ APP_NAME }}</span>
	</div>
</template>

<style scoped>
.layout-logo {
    width: 220px;
	height: 50px;
	display: flex;
	align-items: center;
	justify-content: center;
	box-shadow: rgb(0 21 41 / 2%) 0px 1px 4px;
	color: var(--el-color-primary);
	font-size: 16px;
}
.layout-logo span {
    white-space: nowrap;
    display: inline-block;
}
</style>
