import http from "./http";
import type { IKb, IKbDoc, IKbDocDetail } from "@/typing/kb";

export function listKbs(): Promise<IKb[]> {
  return http.get<IKb[]>("/knowledge-bases");
}

export function createKb(name: string) {
  return http.post<void>("/knowledge-bases", { name });
}

export function deleteKb(kbId: string) {
  return http.delete<void>(`/knowledge-bases/${kbId}`);
}

export function listKbsDocs(kbId: string): Promise<IKbDoc[]> {
  return http.get<IKbDoc[]>(`/knowledge-bases/${kbId}/documents`);
}

export function deleteKbDoc(kbId: string, docId: string) {
  return http.delete<void>(`/knowledge-bases/${kbId}/documents/${docId}`);
}

export function reparseKbDoc(kbId: string, docId: string) {
  return http.post<IKbDoc>(`/knowledge-bases/${kbId}/documents/${docId}/reparse`);
}

export function uploadKbDoc(kbId: string, form: FormData) {
  return http.post<IKbDoc>(`/knowledge-bases/${kbId}/documents`, form);
}

export function listAllDocs(query: string = ""): Promise<IKbDoc[]> {
  return http.get<IKbDoc[]>(`${query ? `/documents?kb_id=${query}` : "/documents"}`);
}

export function getKbDocDetail(docId: string): Promise<IKbDocDetail> {
  return http.get<IKbDocDetail>(`/knowledge-bases/documents/${docId}`);
}
