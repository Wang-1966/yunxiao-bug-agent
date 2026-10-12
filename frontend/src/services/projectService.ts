import api from './api';

export interface Project {
  project_id: string;
  name: string;
  bug_count: number;
  repo_path?: string;
  branch?: string;
  test_command?: string;
}

export interface ProjectConfig {
  name: string;
  repo_path: string;
  branch: string;
  test_command: string;
}

export const projectService = {
  async list(): Promise<Project[]> {
    return (await api.get('/api/projects')).data;
  },
  async save(id: string, config: ProjectConfig) {
    return (await api.put(`/api/projects/${encodeURIComponent(id)}`, config)).data;
  },
  async unbind(id: string) {
    return (await api.delete(`/api/projects/${encodeURIComponent(id)}`)).data;
  },
};
