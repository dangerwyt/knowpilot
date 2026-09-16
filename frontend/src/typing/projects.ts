export interface IProject {
  id: string;
  name: string;
  description?: string;
  created_at: string;
}

export interface IProjectCreate {
  name: string;
  description?: string;
}
