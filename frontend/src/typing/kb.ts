export interface IKb {
  id: string;
  name: string;
  doc_count: number;
  created_at: string;
}

export interface IKbDoc {
  id: string;
  kb_id: string;
  file_name: string;
  status: string;
  chunk_count: number;
  size_bytes?: number | null;
  created_at?: string | null;
}

export interface IChunk {
  seq: number;
  content: string;
}

export interface IKbDocDetail extends IKbDoc {
  content: string | null;
  object_key: string | null;
  chunks: IChunk[];
}
