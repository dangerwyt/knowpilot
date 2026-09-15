// 任务状态 → el-tag type 映射（Element Plus 取值：success/info/warning/danger/primary）
export const TASK_STATUS_MAP = new Map([
  ["pending", { type: "info", name: "待处理" }],
  ["running", { type: "primary", name: "处理中" }],
  ["completed", { type: "success", name: "完成" }],
  ["failed", { type: "danger", name: "失败" }],
  ["cancelled", { type: "warning", name: "已取消" }],
  ["interrupted", { type: "warning", name: "已中断" }],
]);

export const DOC_TYPE_MAP = new Map([
  ["pdf", { type: "danger", name: "Pdf" }],
  ["docx", { type: "warning", name: "Docx" }],
  ["doc", { type: "warning", name: "Doc" }],
  ["txt", { type: "primary", name: "Txt" }],
  ["md", { type: "success", name: "Markdown" }],
]);

export const DOC_STATUS_MAP = new Map([
  ["uploaded", { type: "info", name: "已上传" }],
  ["parsing", { type: "info", name: "解析中" }],
  ["embedding", { type: "primary", name: "嵌入中" }],
  ["ready", { type: "success", name: "就绪" }],
  ["failed", { type: "danger", name: "失败" }],
]);
