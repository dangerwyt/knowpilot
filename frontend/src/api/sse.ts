import { fetchEventSource } from "@microsoft/fetch-event-source";

export interface TaskEventHandlers {
  onEvent: (event: string, data: Record<string, unknown>) => void;
  onError?: (err: unknown) => void;
}

/** 订阅调研任务的 SSE 事件流，返回关闭函数。 */
export function streamTaskEvents(
  taskId: string,
  handlers: TaskEventHandlers,
): () => void {
  const controller = new AbortController();

  const headers: Record<string, string> = {
    Authorization: `Bearer ${localStorage.getItem("token") || ""}`,
    Accept: "text/event-stream",
  };

  fetchEventSource(`/api/v1/tasks/${taskId}/events`, {
    method: "GET",
    signal: controller.signal,
    headers,
    onopen: async (res) => {
      if (res.status !== 200) throw new Error(`SSE 打开失败: ${res.status}`);
    },
    onmessage: (ev) => {
      if (ev.id) headers["Last-Event-ID"] = ev.id;

      let data: Record<string, unknown> = {};
      try {
        data = JSON.parse(ev.data || "{}");
      } catch {
        data = { raw: ev.data };
      }
      handlers.onEvent(ev.event, data);
    },
    onerror: (err) => {
      handlers.onError?.(err);
      throw err; // 触发自动重连
    },
  }).catch(() => {});

  return () => controller.abort();
}
