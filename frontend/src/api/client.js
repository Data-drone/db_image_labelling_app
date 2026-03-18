/**
 * API client for CV Explorer — Phase 1 (project-centric).
 */

import axios from 'axios';

const api = axios.create({
  baseURL: '/api',
  timeout: 30000,
});

// ---------------------------------------------------------------------------
// Projects
// ---------------------------------------------------------------------------
export const fetchProjects = () => api.get('/projects').then(r => r.data);

export const fetchProject = (id) => api.get(`/projects/${id}`).then(r => r.data);

export const createProject = (data) => api.post('/projects', data).then(r => r.data);

export const updateProject = (id, data) => api.patch(`/projects/${id}`, data).then(r => r.data);

export const deleteProject = (id) => api.delete(`/projects/${id}`).then(r => r.data);

export const addProjectClass = (id, className) =>
  api.post(`/projects/${id}/classes`, { class_name: className }).then(r => r.data);

export const fetchProjectStats = (id) => api.get(`/projects/${id}/stats`).then(r => r.data);

export const cloneProject = (id) => api.post(`/projects/${id}/clone`).then(r => r.data);

// ---------------------------------------------------------------------------
// Samples
// ---------------------------------------------------------------------------
export const fetchSamples = (projectId, params = {}) =>
  api.get(`/projects/${projectId}/samples`, { params }).then(r => r.data);

export const fetchNextSample = (projectId) =>
  api.get(`/projects/${projectId}/next`).then(r => r.data);

// ---------------------------------------------------------------------------
// Annotations / Labeling
// ---------------------------------------------------------------------------
export const annotateSample = (projectId, sampleId, data) =>
  api.post(`/projects/${projectId}/samples/${sampleId}/annotate`, data).then(r => r.data);

export const annotateSampleBatch = (projectId, sampleId, annotations) =>
  api.post(`/projects/${projectId}/samples/${sampleId}/annotate-batch`, { annotations }).then(r => r.data);

export const skipSample = (projectId, sampleId) =>
  api.post(`/projects/${projectId}/samples/${sampleId}/skip`).then(r => r.data);

// ---------------------------------------------------------------------------
// Image URLs
// ---------------------------------------------------------------------------
export const sampleImageUrl = (projectId, sampleId) =>
  `/api/projects/${projectId}/samples/${sampleId}/image`;

export const sampleThumbnailUrl = (projectId, sampleId, size = 300) =>
  `/api/projects/${projectId}/samples/${sampleId}/thumbnail?size=${size}`;

// ---------------------------------------------------------------------------
// Browse & Volume navigation (kept from original)
// ---------------------------------------------------------------------------
export const fetchCatalogs = () => api.get('/catalogs').then(r => r.data);

export const fetchSchemas = (catalog) =>
  api.get('/schemas', { params: { catalog } }).then(r => r.data);

export const fetchVolumes = (catalog, schema) =>
  api.get('/volumes', { params: { catalog, schema } }).then(r => r.data);

export const browseDirectory = (path) =>
  api.get('/browse', { params: { path } }).then(r => r.data);

// ---------------------------------------------------------------------------
// Admin
// ---------------------------------------------------------------------------
export const fetchDbStatus = () => api.get('/admin/db-status').then(r => r.data);

export const fetchLakebaseStatus = () => api.get('/admin/lakebase-status').then(r => r.data);

export const provisionLakebase = (projectId, displayName) =>
  api.post('/admin/provision-lakebase', { project_id: projectId, display_name: displayName }).then(r => r.data);

export const connectLakebase = (projectId) =>
  api.post('/admin/connect-lakebase', { project_id: projectId }).then(r => r.data);

export const fetchLakebaseProjectDetail = (projectId) =>
  api.get(`/admin/lakebase-project/${projectId}`).then(r => r.data);

export default api;
