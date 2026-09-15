/** 报告质量（critic 节点产出，随 content 一起存 JSONB） */
export interface IQuality {
  reviewed?: boolean | null;
  score?: number | null;
  passed?: boolean | null;
  issues?: string[];
}

export interface IReportSection {
  title: string;
  content: string;
}

export interface IReportContent {
  title: string;
  sections: IReportSection[];
  quality?: IQuality;
}

export interface IReport {
  id: string;
  task_id: string;
  title: string;
  content: IReportContent;
  version: number;
  created_at: string;
}

export interface ICitation {
  id: string;
  section_id: string;
  source_type: string;
  source_url: string | null;
  source_title: string;
  snippet: string | null;
  document_id?: string | null;
  kb_id?: string | null;
}

/** ReportView 里分组后的引用（按 document_id 聚合，附带被哪些章节引用） */
export interface IGroupedCitation extends ICitation {
  key: string;
  sections: string[];
  snippets: string[];
}
