import { createRouter, createWebHistory } from "vue-router";

import Layout from "../layout/index.vue";

const routes = [
  {
    path: "/",
    redirect: "/projects",
    meta: { hidden: true },
    component: Layout,
    children: [
      {
        path: "/projects",
        component: () => import("../views/ProjectsView.vue"),
        meta: { auth: true, title: "项目" },
      },
      {
        path: "/projects/:id",
        component: () => import("../views/WorkbenchView.vue"),
        meta: { auth: true, hidden: true },
      },
      {
        path: "/reports/:id",
        component: () => import("../views/ReportView.vue"),
        meta: { auth: true, hidden: true },
      },
      {
        path: "/knowledge-bases",
        component: () => import("../views/KnowledgeBaseView.vue"),
        meta: { auth: true, title: "知识库" },
      },
      {
        path: "/documents",
        component: () => import("../views/DocumentView.vue"),
        meta: { auth: true, title: "文档" },
      },
      {
        path: "/tasks",
        component: () => import("../views/TaskView.vue"),
        meta: { auth: true, title: "历史任务" },
      },
    ],
  },
  {
    path: "/login",
    meta: { hidden: true },
    component: () => import("../views/LoginView.vue"),
  },
];

const router = createRouter({
  history: createWebHistory(),
  routes,
});

router.beforeEach((to) => {
  const token = localStorage.getItem("token");
  if (to.meta.auth && !token) return "/login";
  if (to.path === "/login" && token) return "/projects";
});

export default router;
