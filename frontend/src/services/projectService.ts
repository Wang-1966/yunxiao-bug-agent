import api from './api';

export interface Project {
  project_id: string;
  name: string;
  bug_count: number;
  repo_path?: string;
  branch?: string;
  test_command?: string;
  preview_command?: string;
  preview_env_files?: string[];
  preview_port?: number | null;
}

export interface ProjectConfig {
  name: string;
  repo_path: string;
  branch: string;
  test_command: string;
  preview_command: string;
  preview_env_files: string[];
  preview_port?: number | null;
}

export interface DevelopmentStatus {
  status: 'stopped' | 'starting' | 'running' | 'failed';
  url: string | null;
  source: 'project' | 'repair';
  agent_id?: string | null;
  output: string;
  error: string;
}

export interface RepositoryBranches {
  repo_path: string;
  current_branch: string | null;
  branches: string[];
}

export const projectService = {
  async list(): Promise<Project[]> {
    return (await api.get('/api/projects')).data;
  },
  async branches(repoPath: string, signal?: AbortSignal): Promise<RepositoryBranches> {
    return (await api.get('/api/projects/branches', { params: { repo_path: repoPath }, signal })).data;
  },
  async save(id: string, config: ProjectConfig) {
    return (await api.put(`/api/projects/${encodeURIComponent(id)}`, config)).data;
  },
  async unbind(id: string) {
    return (await api.delete(`/api/projects/${encodeURIComponent(id)}`)).data;
  },
  async development(id: string): Promise<DevelopmentStatus> {
    return (await api.get(`/api/projects/${encodeURIComponent(id)}/development`)).data;
  },
  async startDevelopment(id: string): Promise<DevelopmentStatus> {
    return (await api.post(`/api/projects/${encodeURIComponent(id)}/development`)).data;
  },
  async stopDevelopment(id: string): Promise<DevelopmentStatus> {
    return (await api.delete(`/api/projects/${encodeURIComponent(id)}/development`)).data;
  },
};
