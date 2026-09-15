import http from "./http";
import type { ITask, ITaskCreate, IPrecheck } from "@/typing/tasks";

export function taskList(): Promise<ITask[]> {
  return http.get<ITask[]>("/tasks");
}

export function createTask(payload: ITaskCreate): Promise<ITask> {
  return http.post<ITask>("/tasks", payload);
}

export function getTask(taskId: string): Promise<ITask> {
  return http.get<ITask>(`/tasks/${taskId}`);
}

export function resumeTask(taskId: string): Promise<ITask> {
  return http.post<ITask>(`/tasks/${taskId}/resume`, {});
}

export function cancelTask(taskId: string): Promise<ITask> {
  return http.post<ITask>(`/tasks/${taskId}/cancel`);
}

export function precheckTask(payload: {
  objective: string;
  kb_ids?: string[];
}): Promise<IPrecheck> {
  return http.post<IPrecheck>("/tasks/precheck", payload);
}