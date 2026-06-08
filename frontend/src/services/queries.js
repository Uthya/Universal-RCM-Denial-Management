/**
 * react-query hooks per dev endpoint.
 *
 * Pattern: one named hook per endpoint, returning the raw useQuery result.
 * Stale times tuned per UI-plan refresh tables:
 *   - System health → 30s
 *   - Live indicator (TopBar) → 5s
 *   - Operational lists → 30s default
 *   - Telemetry charts → manual refresh (no auto-refetch)
 */

import { useQuery } from '@tanstack/react-query';
import * as api from './api';

// ─── Environment / health ──────────────────────────────────────────────
export function useEnvInfo() {
  return useQuery({
    queryKey: ['env', 'info'],
    queryFn: api.getEnvInfo,
    staleTime: 30_000,
  });
}

export function useHealth() {
  return useQuery({
    queryKey: ['env', 'health'],
    queryFn: api.getHealth,
    refetchInterval: 5_000,
    staleTime: 4_000,
  });
}

// ─── Database State ────────────────────────────────────────────────────
export function useDbOverview() {
  return useQuery({
    queryKey: ['db', 'overview'],
    queryFn: api.getDbOverview,
    staleTime: 30_000,
  });
}

export function useDbTables(params) {
  return useQuery({
    queryKey: ['db', 'tables', params],
    queryFn: () => api.getDbTables(params),
    staleTime: 30_000,
  });
}

export function useDbTableDetail(name) {
  return useQuery({
    queryKey: ['db', 'tables', name, 'detail'],
    queryFn: () => api.getDbTableDetail(name),
    enabled: !!name,
    staleTime: 30_000,
  });
}

export function useDbMaterializedViews() {
  return useQuery({
    queryKey: ['db', 'materialized-views'],
    queryFn: api.getDbMaterializedViews,
    staleTime: 30_000,
  });
}

export function useDbFunctions() {
  return useQuery({
    queryKey: ['db', 'functions'],
    queryFn: api.getDbFunctions,
    staleTime: 60_000,
  });
}

export function useDbIndexes(params) {
  return useQuery({
    queryKey: ['db', 'indexes', params],
    queryFn: () => api.getDbIndexes(params),
    staleTime: 60_000,
  });
}

export function useDbMigrations() {
  return useQuery({
    queryKey: ['db', 'migrations'],
    queryFn: api.getDbMigrations,
    staleTime: 5 * 60_000,
  });
}

// ─── Parsing Telemetry ─────────────────────────────────────────────────
// Manual-refresh by default per UI-plan §3 Page 4 (charts shouldn't auto-refresh
// while the user is reading them). staleTime large so the data sticks around.
const TELEMETRY_OPTS = { staleTime: 60_000, refetchOnWindowFocus: false };

export function useTelemetryDrops(params) {
  return useQuery({ queryKey: ['telemetry', 'drops', params],
    queryFn: () => api.getTelemetryDrops(params), ...TELEMETRY_OPTS });
}
export function useTelemetryDropReasons(params) {
  return useQuery({ queryKey: ['telemetry', 'drop-reasons', params],
    queryFn: () => api.getTelemetryDropReasons(params), ...TELEMETRY_OPTS });
}
export function useTelemetryUnhandledSegments(params) {
  return useQuery({ queryKey: ['telemetry', 'unhandled-segments', params],
    queryFn: () => api.getTelemetryUnhandledSegments(params), ...TELEMETRY_OPTS });
}
export function useTelemetryCasStride(params) {
  return useQuery({ queryKey: ['telemetry', 'cas-stride', params],
    queryFn: () => api.getTelemetryCasStride(params), ...TELEMETRY_OPTS });
}
export function useTelemetryEncoding(params) {
  return useQuery({ queryKey: ['telemetry', 'encoding', params],
    queryFn: () => api.getTelemetryEncoding(params), ...TELEMETRY_OPTS });
}
export function useTelemetryValidatorTiers(params) {
  return useQuery({ queryKey: ['telemetry', 'validator-tiers', params],
    queryFn: () => api.getTelemetryValidatorTiers(params), ...TELEMETRY_OPTS });
}
export function useTelemetryReparseLog(params) {
  return useQuery({ queryKey: ['telemetry', 'reparse-log', params],
    queryFn: () => api.getTelemetryReparseLog(params), ...TELEMETRY_OPTS });
}

// ─── Home dashboard ────────────────────────────────────────────────────
export function useRecentUploads(params) {
  return useQuery({
    queryKey: ['uploads', 'recent', params],
    queryFn: () => api.getRecentUploads(params),
    refetchInterval: 30_000,
    staleTime: 25_000,
  });
}

export function useModelsRegistry() {
  return useQuery({
    queryKey: ['models', 'registry'],
    queryFn: api.getModelsRegistry,
    refetchInterval: 60_000,
    staleTime: 30_000,
  });
}

export function useJobsSummary() {
  return useQuery({
    queryKey: ['jobs', 'summary'],
    queryFn: api.getJobsSummary,
    refetchInterval: 10_000,
    staleTime: 8_000,
  });
}

// ─── EDI Inspector ─────────────────────────────────────────────────────
export function useEdiFiles(params) {
  return useQuery({
    queryKey: ['edi', 'files', params],
    queryFn: () => api.getEdiFiles(params),
    staleTime: 30_000,
  });
}

export function useEdiFileDetail(id) {
  return useQuery({
    queryKey: ['edi', 'files', id, 'detail'],
    queryFn: () => api.getEdiFileDetail(id),
    enabled: !!id,
    staleTime: 30_000,
  });
}

export function useEdiFileSegments(id, params) {
  return useQuery({
    queryKey: ['edi', 'files', id, 'segments', params],
    queryFn: () => api.getEdiFileSegments(id, params),
    enabled: !!id,
    staleTime: 30_000,
  });
}

export function useEdiFileEvents(id, params) {
  return useQuery({
    queryKey: ['edi', 'files', id, 'events', params],
    queryFn: () => api.getEdiFileEvents(id, params),
    enabled: !!id,
    staleTime: 30_000,
  });
}

// ─── Claims Browser ────────────────────────────────────────────────────
export function useClaims(params) {
  return useQuery({
    queryKey: ['claims', 'list', params],
    queryFn: () => api.getClaims(params),
    staleTime: 30_000,
  });
}

export function useClaimDetail(id) {
  return useQuery({
    queryKey: ['claims', id, 'detail'],
    queryFn: () => api.getClaimDetail(id),
    enabled: !!id,
    staleTime: 30_000,
  });
}

export function useClaimLines(id) {
  return useQuery({
    queryKey: ['claims', id, 'lines'],
    queryFn: () => api.getClaimLines(id),
    enabled: !!id,
    staleTime: 60_000,
  });
}

export function useClaimDiagnoses(id) {
  return useQuery({
    queryKey: ['claims', id, 'diagnoses'],
    queryFn: () => api.getClaimDiagnoses(id),
    enabled: !!id,
    staleTime: 60_000,
  });
}

export function useClaimRemits(id) {
  return useQuery({
    queryKey: ['claims', id, 'remits'],
    queryFn: () => api.getClaimRemits(id),
    enabled: !!id,
    staleTime: 60_000,
  });
}
