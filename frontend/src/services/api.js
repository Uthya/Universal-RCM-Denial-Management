/**
 * axios client for the v2 dev console.
 *
 * All endpoints live under /api/dev/* — no production user-facing surface.
 * No auth header (local-only deployment per UI plan §8).
 *
 * Add a new endpoint by:
 *   1. Build the backend route in src/rcm/routers/dev/*.py
 *   2. Add a TestClient test in tests/integration/test_dev_endpoints.py
 *   3. Add a thin wrapper here that returns the bare response.data
 *   4. Consume from a react-query hook in src/services/queries.js
 */

import axios from 'axios';

const api = axios.create({
  baseURL: import.meta.env.VITE_API_BASE_URL || '/api/dev',
  timeout: 30000,
  headers: {
    'Content-Type': 'application/json',
  },
});

// Surface error.response.data.detail to the UI so backend HTTPException
// messages reach the user verbatim (debugging the parser is hard if you
// can't see the validation error).
api.interceptors.response.use(
  (response) => response,
  (error) => {
    if (error.response?.data?.detail) {
      error.message = error.response.data.detail;
    }
    return Promise.reject(error);
  }
);

// ─── Page 12: Environment ──────────────────────────────────────────────
export const getEnvInfo = () => api.get('/env/info').then((r) => r.data);
export const getHealth = () => api.get('/env/health').then((r) => r.data);

// ─── Page 9: Database State ────────────────────────────────────────────
export const getDbOverview = () => api.get('/db/overview').then((r) => r.data);
export const getDbTables = ({ kind, sort = 'name', limit = 200, offset = 0 } = {}) =>
  api.get('/db/tables', { params: { kind, sort, limit, offset } }).then((r) => r.data);
export const getDbTableDetail = (name) =>
  api.get(`/db/tables/${encodeURIComponent(name)}`).then((r) => r.data);
export const getDbMaterializedViews = () =>
  api.get('/db/materialized-views').then((r) => r.data);
export const refreshMaterializedView = (name) =>
  api.post(`/db/refresh-mv/${encodeURIComponent(name)}?confirm=true`).then((r) => r.data);
export const getDbFunctions = () =>
  api.get('/db/functions').then((r) => r.data);
export const getDbIndexes = ({ limit = 200, offset = 0 } = {}) =>
  api.get('/db/indexes', { params: { limit, offset } }).then((r) => r.data);
export const getDbMigrations = () =>
  api.get('/db/migrations').then((r) => r.data);
export const runSqlQuery = (sql) =>
  api.post('/db/query?confirm=true', { sql }).then((r) => r.data);

// ─── Page 4: Parsing Telemetry ─────────────────────────────────────────
export const getTelemetryDrops = ({ days = 30, group_by = 'day' } = {}) =>
  api.get('/telemetry/drops', { params: { days, group_by } }).then((r) => r.data);
export const getTelemetryDropReasons = ({ days = 7, top = 20 } = {}) =>
  api.get('/telemetry/drop-reasons', { params: { days, top } }).then((r) => r.data);
export const getTelemetryUnhandledSegments = ({ days = 7, top = 20 } = {}) =>
  api.get('/telemetry/unhandled-segments', { params: { days, top } }).then((r) => r.data);
export const getTelemetryCasStride = ({ days = 7 } = {}) =>
  api.get('/telemetry/cas-stride-distribution', { params: { days } }).then((r) => r.data);
export const getTelemetryEncoding = ({ days = 7 } = {}) =>
  api.get('/telemetry/encoding-distribution', { params: { days } }).then((r) => r.data);
export const getTelemetryValidatorTiers = ({ days = 7 } = {}) =>
  api.get('/telemetry/validator-tiers', { params: { days } }).then((r) => r.data);
export const getTelemetryReparseLog = ({ days = 30, limit = 50 } = {}) =>
  api.get('/telemetry/reparse-log', { params: { days, limit } }).then((r) => r.data);

// ─── Page 1: Home dashboard ────────────────────────────────────────────
export const getRecentUploads = ({ limit = 10 } = {}) =>
  api.get('/uploads/recent', { params: { limit } }).then((r) => r.data);
export const getModelsRegistry = () =>
  api.get('/models/registry').then((r) => r.data);
export const getJobsSummary = () =>
  api.get('/jobs/summary').then((r) => r.data);

// ─── Page 2: EDI Inspector ─────────────────────────────────────────────
export const uploadEdi = (file) => {
  const fd = new FormData();
  fd.append('file', file);
  return api.post('/edi/upload?confirm=true', fd, {
    headers: { 'Content-Type': 'multipart/form-data' },
    timeout: 120_000,
  }).then((r) => r.data);
};
export const getEdiFiles = ({ status, variant, q, limit = 50, offset = 0 } = {}) =>
  api.get('/edi/files', { params: { status, variant, q, limit, offset } })
     .then((r) => r.data);
export const getEdiFileDetail = (id) =>
  api.get(`/edi/files/${id}`).then((r) => r.data);
export const getEdiFileSegments = (id, { handler_status, limit = 200, offset = 0 } = {}) =>
  api.get(`/edi/files/${id}/segments`, { params: { handler_status, limit, offset } })
     .then((r) => r.data);
export const getEdiFileEvents = (id, { event_type, limit = 200, offset = 0 } = {}) =>
  api.get(`/edi/files/${id}/events`, { params: { event_type, limit, offset } })
     .then((r) => r.data);

// ─── Page 3: Claims Browser ────────────────────────────────────────────
export const getClaims = ({
  variant, subtype, payer_id, claim_status, q, limit = 50, offset = 0,
} = {}) =>
  api.get('/claims', {
    params: { variant, subtype, payer_id, claim_status, q, limit, offset },
  }).then((r) => r.data);
export const getClaimDetail = (id) =>
  api.get(`/claims/${id}`).then((r) => r.data);
export const getClaimLines = (id) =>
  api.get(`/claims/${id}/lines`).then((r) => r.data);
export const getClaimDiagnoses = (id) =>
  api.get(`/claims/${id}/diagnoses`).then((r) => r.data);
export const getClaimRemits = (id) =>
  api.get(`/claims/${id}/remits`).then((r) => r.data);

export default api;
