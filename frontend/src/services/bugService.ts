import api from './api';
import type { Bug, BugSupplement, TaskSubmitRequest } from '../types';

export const bugService = {
  // 获取 Bug 列表
  async list(params?: {
    page?: number;
    page_size?: number;
    status?: string;
    priority?: string;
    search?: string;
    exclude_closed_yunxiao?: boolean;
    source_status?: string;
  }) {
    const response = await api.get('/api/bugs', { params });
    return response.data;
  },

  async sourceStatuses(): Promise<string[]> {
    const response = await api.get('/api/bugs/filters/source-statuses');
    return response.data;
  },

  async yunxiaoStatuses(bugId: string): Promise<string[]> {
    const response = await api.get(`/api/bugs/${encodeURIComponent(bugId)}/yunxiao-statuses`);
    return response.data.statuses;
  },

  async updateYunxiaoStatus(bugId: string, status: string): Promise<{ source_status: string }> {
    const response = await api.put(`/api/bugs/${encodeURIComponent(bugId)}/yunxiao-status`, { status });
    return response.data;
  },

  async dashboardStats(): Promise<{ total: number; critical: number; resolved: number }> {
    const response = await api.get('/api/bugs/stats/dashboard');
    return response.data;
  },

  // 获取 Bug 详情
  async get(bugId: string) {
    const response = await api.get(`/api/bugs/${encodeURIComponent(bugId)}/details`);
    return response.data;
  },

  async supplement(bugId: string): Promise<BugSupplement> {
    const response = await api.get(`/api/bugs/${encodeURIComponent(bugId)}/supplement`);
    return response.data;
  },

  async saveSupplement(bugId: string, supplement: BugSupplement): Promise<BugSupplement> {
    const response = await api.put(`/api/bugs/${encodeURIComponent(bugId)}/supplement`, supplement);
    return response.data;
  },

  // 创建 Bug
  async create(bug: {
    bug_id: string;
    title: string;
    description?: string;
    priority: string;
    source?: string;
    source_url?: string;
    repo_path?: string;
    branch?: string;
    test_command?: string;
  }) {
    const response = await api.post('/api/bugs', bug);
    return response.data;
  },

  // 更新 Bug
  async update(bugId: string, updates: Partial<Bug>) {
    const response = await api.patch(`/api/bugs/${bugId}`, updates);
    return response.data;
  },

  // 同步 Bug
  async sync() {
    const response = await api.post('/api/bugs/sync');
    return response.data;
  },

  // 提交修复任务
  async submitTask(request: TaskSubmitRequest) {
    const response = await api.post('/api/agents/submit-task', request);
    return response.data;
  },
};
