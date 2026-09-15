export interface ITask {
  id: string;
  objective: string;
  status: string;
  project_id: string;
  report_id: string | null;
  created_at: string;
  kb_ids?: string[] | null;
  title?: string | null;
  score?: number | null;
  passed?: boolean | null;
  elapsed_seconds?: number | null;
  error?: string | null;
}

export interface ITaskCreate {
  project_id: string;
  objective: string;
  sources?: "all" | "web" | "kb";
  kb_ids?: string[];
  title?: string;
}

export interface IPrecheck {
  has_material: boolean | null;
  material_count: number;
}
