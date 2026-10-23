import axios, { type AxiosError } from 'axios';
import type {
  Workspace,
  WorkspaceCreated,
  Instrument,
  CalculationResult,
  ApiError,
} from '@/types/api';
import {
  mockOpenWorkspace,
  mockGetWorkspace,
  mockFetchInstrument,
  mockAddTrade,
  mockUpdateTrade,
  mockDeleteTrade,
  mockRunCalculation,
  mockGetCalculation,
} from '@/mocks/data';

const API_BASE_URL: string = import.meta.env.VITE_API_BASE_URL ?? '';
export const USE_MOCKS: boolean = import.meta.env.VITE_USE_MOCKS === 'true';

const api = axios.create({
  baseURL: API_BASE_URL,
  timeout: 15_000,
  headers: { 'Content-Type': 'application/json' },
  withCredentials: true,
});

function snakeToCamel(str: string): string {
  return str.replace(/_([a-z])/g, (_, c) => c.toUpperCase());
}

function camelToSnake(str: string): string {
  return str.replace(/[A-Z]/g, (c) => `_${c.toLowerCase()}`);
}

function keysToCamel(obj: unknown): unknown {
  if (Array.isArray(obj)) return obj.map(keysToCamel);
  if (obj !== null && typeof obj === 'object' && !(obj instanceof Date)) {
    return Object.fromEntries(
      Object.entries(obj as Record<string, unknown>).map(([k, v]) => [
        snakeToCamel(k),
        keysToCamel(v),
      ]),
    );
  }
  return obj;
}

function keysToSnake(obj: unknown): unknown {
  if (Array.isArray(obj)) return obj.map(keysToSnake);
  if (obj !== null && typeof obj === 'object' && !(obj instanceof Date)) {
    return Object.fromEntries(
      Object.entries(obj as Record<string, unknown>).map(([k, v]) => [
        camelToSnake(k),
        keysToSnake(v),
      ]),
    );
  }
  return obj;
}

api.interceptors.request.use((config) => {
  if (config.data && typeof config.data === 'object') {
    config.data = keysToSnake(config.data);
  }
  return config;
});

api.interceptors.response.use(
  (response) => {
    if (response.data && typeof response.data === 'object') {
      response.data = keysToCamel(response.data);
    }
    return response;
  },
  (error: AxiosError) => Promise.reject(error),
);

let csrfToken: string | null = null;
let workspaceId: string | null = null;

export function getCsrfToken(): string | null {
  return csrfToken;
}

export function getWorkspaceId(): string | null {
  return workspaceId;
}

function mutHeaders(): Record<string, string> {
  const headers: Record<string, string> = {};
  if (csrfToken) {
    headers['X-CSRF-Token'] = csrfToken;
  }
  return headers;
}

export function extractApiError(err: unknown): ApiError | null {
  if (!axios.isAxiosError(err) || !err.response) return null;
  const { status, data } = err.response;
  const body = keysToCamel(data) as Record<string, unknown>;
  if (typeof body?.code === 'string' && typeof body?.message === 'string') {
    return {
      status,
      code: body.code as string,
      message: body.message as string,
      details: Array.isArray(body.details) ? body.details as ApiError['details'] : [],
    };
  }
  return {
    status,
    code: 'UNKNOWN',
    message: `Ошибка сервера (HTTP ${status})`,
    details: [],
  };
}

export function getErrorMessage(err: unknown): string {
  const apiErr = extractApiError(err);
  if (apiErr) {
    if (apiErr.details.length > 0) {
      return apiErr.details.map((d) => d.message).join('; ');
    }
    return apiErr.message;
  }
  if (axios.isAxiosError(err)) {
    if (err.code === 'ERR_NETWORK' || err.code === 'ECONNABORTED') {
      return 'Нет соединения с сервером. Проверьте подключение и попробуйте снова.';
    }
    return err.message;
  }
  if (err instanceof Error) return err.message;
  return 'Неизвестная ошибка';
}

export async function openWorkspace(): Promise<WorkspaceCreated> {
  if (USE_MOCKS) {
    const ws = await mockOpenWorkspace();
    csrfToken = ws.csrfToken;
    workspaceId = ws.id;
    return ws;
  }
  const { data } = await api.post<WorkspaceCreated>('/v1/workspaces');
  csrfToken = data.csrfToken;
  workspaceId = data.id;
  return data;
}

export async function getWorkspace(): Promise<Workspace> {
  if (USE_MOCKS) {
    return mockGetWorkspace();
  }
  if (!workspaceId) throw new Error('Сессия не открыта');
  const { data } = await api.get<Workspace>(`/v1/workspaces/${workspaceId}`);
  return data;
}

export async function addTrade(
  isin: string,
  quantity: number,
  expectedRevision: number,
): Promise<Workspace> {
  if (USE_MOCKS) {
    return mockAddTrade(isin, quantity, expectedRevision);
  }
  if (!workspaceId) throw new Error('Сессия не открыта');
  const { data } = await api.post<Workspace>(
    `/v1/workspaces/${workspaceId}/trades`,
    { isin, quantity, expectedRevision },
    { headers: mutHeaders() },
  );
  return data;
}

export async function updateTrade(
  tradeId: string,
  expectedRevision: number,
  fields: { isin?: string; quantity?: number },
): Promise<Workspace> {
  if (USE_MOCKS) {
    return mockUpdateTrade(tradeId, expectedRevision, fields);
  }
  if (!workspaceId) throw new Error('Сессия не открыта');
  const { data } = await api.patch<Workspace>(
    `/v1/workspaces/${workspaceId}/trades/${tradeId}`,
    { ...fields, expectedRevision },
    { headers: mutHeaders() },
  );
  return data;
}

export async function deleteTrade(
  tradeId: string,
  expectedRevision: number,
): Promise<Workspace> {
  if (USE_MOCKS) {
    return mockDeleteTrade(tradeId, expectedRevision);
  }
  if (!workspaceId) throw new Error('Сессия не открыта');
  const { data } = await api.delete<Workspace>(
    `/v1/workspaces/${workspaceId}/trades/${tradeId}`,
    {
      params: { expected_revision: expectedRevision },
      headers: mutHeaders(),
    },
  );
  return data;
}

export async function fetchInstrument(isin: string): Promise<Instrument | null> {
  if (USE_MOCKS) {
    return mockFetchInstrument(isin);
  }
  try {
    const { data } = await api.get<Instrument>(
      `/v1/instruments/${encodeURIComponent(isin)}`,
    );
    return data;
  } catch (err) {
    if (axios.isAxiosError(err) && err.response?.status === 404) {
      return null;
    }
    throw err;
  }
}

export async function runCalculation(
  expectedRevision: number,
  valuationDate?: string,
): Promise<CalculationResult> {
  if (USE_MOCKS) {
    return mockRunCalculation(expectedRevision, valuationDate);
  }
  if (!workspaceId) throw new Error('Сессия не открыта');
  const body: Record<string, unknown> = { expectedRevision };
  if (valuationDate) body.valuationDate = valuationDate;
  const { data } = await api.post<CalculationResult>(
    `/v1/workspaces/${workspaceId}/calculations`,
    body,
    { headers: mutHeaders() },
  );
  return data;
}

export async function getCalculation(calculationId: string): Promise<CalculationResult> {
  if (USE_MOCKS) {
    return mockGetCalculation(calculationId);
  }
  const { data } = await api.get<CalculationResult>(
    `/v1/calculations/${calculationId}`,
  );
  return data;
}
