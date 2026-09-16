import http from "./http";
import type { IProject, IProjectCreate } from "@/typing/projects";

export function projectList(): Promise<IProject[]> {
  return http.get<IProject[]>("/projects");
}

export function createProject(payload: IProjectCreate): Promise<IProject> {
  return http.post<IProject>("/projects", payload);
}
