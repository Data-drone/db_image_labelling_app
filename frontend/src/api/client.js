/**
 * API client for CV Dataset Explorer backend.
 * All endpoints are proxied through Vite dev server to http://localhost:8000.
 */

import axios from 'axios';

const api = axios.create({
  baseURL: '/api',
  timeout: 30000,
});

// ---------------------------------------------------------------------------
// Datasets
// ---------------------------------------------------------------------------
export const fetchDatasets = () => api.get('/datasets').then(r => r.data);

export const fetchDataset = (id) => api.get(`/datasets/${id}`).then(r => r.data);

export const createDataset = (data) => api.post('/datasets', data).then(r => r.data);

export const deleteDataset = (id) => api.delete(`/datasets/${id}`).then(r => r.data);

export const fetchDatasetStats = (id) => api.get(`/datasets/${id}/stats`).then(r => r.data);

// ---------------------------------------------------------------------------
// Samples
// ---------------------------------------------------------------------------
export const fetchSamples = (datasetId, params = {}) =>
  api.get(`/datasets/${datasetId}/samples`, { params }).then(r => r.data);

export const fetchSample = (id) => api.get(`/samples/${id}`).then(r => r.data);

// ---------------------------------------------------------------------------
// Annotations
// ---------------------------------------------------------------------------
export const fetchAnnotations = (sampleId) =>
  api.get(`/samples/${sampleId}/annotations`).then(r => r.data);

export const createAnnotation = (data) =>
  api.post('/annotations', data).then(r => r.data);

export const createAnnotationsBatch = (annotations) =>
  api.post('/annotations/batch', annotations).then(r => r.data);

export const deleteAnnotation = (id) =>
  api.delete(`/annotations/${id}`).then(r => r.data);

// ---------------------------------------------------------------------------
// Tags
// ---------------------------------------------------------------------------
export const fetchTags = (sampleId) =>
  api.get(`/samples/${sampleId}/tags`).then(r => r.data);

export const createTag = (data) => api.post('/tags', data).then(r => r.data);

export const deleteTag = (id) => api.delete(`/tags/${id}`).then(r => r.data);

// ---------------------------------------------------------------------------
// Image URLs
// ---------------------------------------------------------------------------
export const imageUrl = (sampleId) => `/images/${sampleId}`;

export const thumbnailUrl = (sampleId, size = 300) =>
  `/images/${sampleId}/thumbnail?size=${size}`;

// ---------------------------------------------------------------------------
// Browse & Volume navigation
// ---------------------------------------------------------------------------
export const fetchCatalogs = () => api.get('/catalogs').then(r => r.data);

export const fetchSchemas = (catalog) =>
  api.get('/schemas', { params: { catalog } }).then(r => r.data);

export const fetchVolumes = (catalog, schema) =>
  api.get('/volumes', { params: { catalog, schema } }).then(r => r.data);

export const browseDirectory = (path) =>
  api.get('/browse', { params: { path } }).then(r => r.data);

export default api;
