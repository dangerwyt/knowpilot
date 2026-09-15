import { createApp } from "vue";
import { createPinia } from "pinia";
import ElementPlus from "element-plus";
import zhCn from "element-plus/es/locale/lang/zh-cn";
import "element-plus/dist/index.css";
import "element-plus/theme-chalk/dark/css-vars.css";
import App from "./App.vue";
import router from "./router";

import * as ElementPlusIconsVue from "@element-plus/icons-vue";

const app = createApp(App);

// 图标全局注册：必须注册在下面真正 mount 的那个实例上，
// 否则注册的是一个"没被挂载的实例"，等于没注册（曾踩：又 createApp 了一次）
for (const [key, component] of Object.entries(ElementPlusIconsVue)) {
  app.component(key, component);
}

// 暗色模式（与 Element Plus dark css-vars 配合）
// document.documentElement.classList.add('dark')

app
  .use(createPinia())
  .use(router)
  .use(ElementPlus, { locale: zhCn })
  .mount("#app");
