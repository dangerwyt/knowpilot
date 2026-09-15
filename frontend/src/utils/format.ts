import { dayjs } from "element-plus";
import { DOC_TYPE_MAP, DOC_STATUS_MAP } from "@/utils/constant";

export const formatSize = (size: number) => {
  if (!size) {
    return "0";
  }
  if (size < 1024) {
    return `${size} B`;
  }
  if (size < 1024 * 1024) {
    return `${(size / 1024).toFixed(2)} KB`;
  }
  if (size < 1024 * 1024 * 1024) {
    return `${(size / 1024 / 1024).toFixed(2)} MB`;
  }
  if (size < 1024 * 1024 * 1024 * 1024) {
    return `${(size / 1024 / 1024 / 1024).toFixed(2)} GB`;
  }
};

export const formatDocType = (fileName: string) => {
  const end = fileName.split(".")[1];
  if (!DOC_TYPE_MAP.has(end)) {
    return { type: "info", name: "-" };
  } else {
    return {
      type: DOC_TYPE_MAP.get(end)?.type,
      name: DOC_TYPE_MAP.get(end)?.name,
    };
  }
};

export const formatDocStatus = (status: string) => {
  if (!DOC_STATUS_MAP.has(status)) {
    return { type: "info", name: "-" };
  } else {
    return {
      type: DOC_STATUS_MAP.get(status)?.type,
      name: DOC_STATUS_MAP.get(status)?.name,
    };
  }
};

export const formatDate = (
  date: string,
  format: string = "YYYY-MM-DD HH:mm:ss",
) => {
  return dayjs(date).format(format);
};

/** 展示用：把 markdown 文本扁平化到单行纯文本（剥 #/>/-/强调/链接/代码），chunk 卡片用。 */
export function stripMarkdown(s: string): string {
  return s
    .replace(/^\s{0,3}#{1,6}\s+/gm, "") // 标题 #/##/###
    .replace(/^\s{0,3}>\s?/gm, "") // 引用 >
    .replace(/^\s{0,3}[-+*]\s+/gm, "") // 列表 -/+/*
    .replace(/`([^`]+)`/g, "$1") // 行内代码 `x`
    .replace(/\*\*([^*]+)\*\*/g, "$1") // 加粗 **x**
    .replace(/\*([^*]+)\*/g, "$1") // 斜体 *x*
    .replace(/\[([^\]]+)\]\([^)]+\)/g, "$1") // 链接 [text](url) → text
    .replace(/\s+/g, " ") // 多行合并成一行（CSS nowrap 配合）
    .trim();
}
