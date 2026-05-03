/**
 * API client for CV Explorer — Phase 1 (project-centric).
 */

import axios from 'axios';
import { humanizeApiError } from './errors';

const api = axios.create({
  baseURL: '/api',
  timeout: 30000,
});

api.interceptors.response.use(
  (r) => r,
  (err) => {
    if (err && typeof err === 'object') {
      err.userMessage = humanizeApiError(err);
    }
    return Promise.reject(err);
  },
);

export { humanizeApiError } from './errors';

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

export const fetchDetailedProjectStats = (id) => api.get(`/projects/${id}/stats/detailed`).then(r => r.data);

export const cloneProject = (id) => api.post(`/projects/${id}/clone`).then(r => r.data);

export const exportProject = (id, exportVolume) =>
  api.post(`/projects/${id}/export`, { export_volume: exportVolume }, { timeout: 300000 }).then(r => r.data);

// ---------------------------------------------------------------------------
// Samples
// ---------------------------------------------------------------------------
export const fetchSamples = (projectId, params = {}) =>
  api.get(`/projects/${projectId}/samples`, { params }).then(r => r.data);

export const fetchSample = (projectId, sampleId) =>
  api.get(`/projects/${projectId}/samples/${sampleId}`).then(r => r.data);

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

export const fetchSampleHistory = (projectId, sampleId) =>
  api.get(`/projects/${projectId}/samples/${sampleId}/history`).then(r => r.data);

// ---------------------------------------------------------------------------
// Image URLs
// ---------------------------------------------------------------------------
export const sampleImageUrl = (projectId, sampleId) =>
  `/api/projects/${projectId}/samples/${sampleId}/image`;

export const sampleThumbnailUrl = (projectId, sampleId, size = 300) =>
  `/api/projects/${projectId}/samples/${sampleId}/thumbnail?size=${size}`;

// ---------------------------------------------------------------------------
// Pre-annotation / Inference
// ---------------------------------------------------------------------------
/** Workspace defaults from env (e.g. SERVING_ENDPOINT); no project id required. */
export const fetchInferenceDefaults = () =>
  api.get('/inference-defaults').then((r) => r.data);

export const fetchEndpointStatus = (projectId) =>
  api.get(`/projects/${projectId}/endpoint-status`).then(r => r.data);

export const predictSample = (projectId, sampleId) =>
  api.get(`/projects/${projectId}/samples/${sampleId}/predict`).then(r => r.data);

export const preAnnotateProject = (projectId, params = {}) =>
  api.post(`/projects/${projectId}/pre-annotate`, params, { timeout: 300000 }).then(r => r.data);

/**
 * SSE-streaming pre-annotate. Calls onProgress({completed,failed,skipped,total,current})
 * for each sample and resolves with the final counters on completion.
 */
export function preAnnotateProjectStream(projectId, params = {}, { onProgress, signal } = {}) {
  return new Promise((resolve, reject) => {
    fetch(`/api/projects/${projectId}/pre-annotate`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json', Accept: 'text/event-stream' },
      body: JSON.stringify(params),
      signal,
    })
      .then((res) => {
        if (!res.ok) {
          return res.text().then((body) => {
            let detail = body;
            try { detail = JSON.parse(body).detail || body; } catch {}
            reject(new Error(detail));
          });
        }
        const reader = res.body.getReader();
        const decoder = new TextDecoder();
        let buf = '';
        let lastData = null;

        function pump() {
          reader.read().then(({ done, value }) => {
            if (done) {
              resolve(lastData || { completed: 0, failed: 0, skipped: 0, total: 0 });
              return;
            }
            buf += decoder.decode(value, { stream: true });
            const lines = buf.split('\n');
            buf = lines.pop();
            let eventType = 'progress';
            for (const line of lines) {
              if (line.startsWith('event: ')) eventType = line.slice(7).trim();
              else if (line.startsWith('data: ')) {
                try {
                  const data = JSON.parse(line.slice(6));
                  lastData = data;
                  if (eventType === 'progress' && onProgress) onProgress(data);
                  if (eventType === 'done') { resolve(data); return; }
                } catch {}
              }
            }
            pump();
          }).catch(reject);
        }
        pump();
      })
      .catch(reject);
  });
}

export const acceptDraftsSample = (projectId, sampleId) =>
  api.post(`/projects/${projectId}/samples/${sampleId}/accept-drafts`).then(r => r.data);

export const clearDraftsSample = (projectId, sampleId) =>
  api.post(`/projects/${projectId}/samples/${sampleId}/clear-drafts`).then(r => r.data);

export const acceptAllDrafts = (projectId) =>
  api.post(`/projects/${projectId}/drafts/accept-all`).then(r => r.data);

export const clearAllModelDrafts = (projectId) =>
  api.post(`/projects/${projectId}/drafts/clear-all`).then(r => r.data);

export const bulkAcceptDrafts = (projectId, sampleIds) =>
  api.post(`/projects/${projectId}/drafts/bulk-accept`, { sample_ids: sampleIds }).then(r => r.data);

export const fetchInferenceSettings = (projectId) =>
  api.get(`/projects/${projectId}/settings`).then(r => r.data);

export const enqueuePreannotateJob = (projectId, params = {}) =>
  api.post(`/projects/${projectId}/pre-annotate-async`, params, { timeout: 60000 }).then(r => r.data);

export const fetchLatestPreannotateRun = (projectId) =>
  api.get(`/projects/${projectId}/pre-annotate-runs/latest`).then(r => r.data);

export const fetchPreannotateRun = (projectId, runId) =>
  api.get(`/projects/${projectId}/pre-annotate-runs/${runId}`).then(r => r.data);

// ---------------------------------------------------------------------------
// Finetuning
// ---------------------------------------------------------------------------
export const triggerFinetune = (projectId, exportPath) =>
  api.post(`/projects/${projectId}/finetune`, { export_path: exportPath }, { timeout: 60000 }).then(r => r.data);

export const fetchLatestFinetuneRun = (projectId) =>
  api.get(`/projects/${projectId}/finetune-runs/latest`).then(r => r.data);

export const fetchFinetuneRun = (projectId, runId) =>
  api.get(`/projects/${projectId}/finetune-runs/${runId}`).then(r => r.data);

// ---------------------------------------------------------------------------
// App config
// ---------------------------------------------------------------------------
export const fetchAppConfig = () => api.get('/config').then(r => r.data);

// ---------------------------------------------------------------------------
// Browse & Volume navigation (kept from original)
// ---------------------------------------------------------------------------
export const fetchCatalogs = () => api.get('/catalogs').then(r => r.data);

export const fetchSchemas = (catalog) =>
  api.get('/schemas', { params: { catalog } }).then(r => r.data);

export const fetchVolumes = (catalog, schema) =>
  api.get('/volumes', { params: { catalog, schema } }).then(r => r.data);

export const browseDirectory = (path, { page, page_size } = {}) =>
  api.get('/browse', { params: { path, page, page_size } }).then(r => r.data);

// ---------------------------------------------------------------------------
// Embeddings / Similarity
// ---------------------------------------------------------------------------
export const startEmbeddingRun = (projectId, params = {}) =>
  api.post(`/projects/${projectId}/generate-embeddings`, params, { timeout: 60000 }).then(r => r.data);

export const fetchLatestEmbeddingRun = (projectId) =>
  api.get(`/projects/${projectId}/embedding-runs/latest`).then(r => r.data);

export const fetchEmbeddingRun = (projectId, runId) =>
  api.get(`/projects/${projectId}/embedding-runs/${runId}`).then(r => r.data);

export const fetchSimilarSamples = (projectId, sampleId, limit = 24) =>
  api.get(`/projects/${projectId}/samples/${sampleId}/similar`, { params: { limit } }).then(r => r.data);

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
