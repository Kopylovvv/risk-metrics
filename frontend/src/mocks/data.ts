import type {
  Instrument,
  Trade,
  Workspace,
  WorkspaceCreated,
  CalculationResult,
  CalculationRow,
} from '../types/api';
import { generateId, validateQuantity } from '../utils/helpers.ts';

export const mockInstruments: Instrument[] = [
  {
    isin: 'RU000A1038V6',
    name: 'ДЕМО ОФЗ, фиксированный купон',
    instrumentType: 'bond',
    currency: 'RUB',
    nominal: '1000',
    pricePct: '95.0',
    couponType: 'fixed',
    couponFrequency: 2,
    dataAsOf: '2026-10-20',
    provider: 'cbonds',
  },
  {
    isin: 'RU000A105A95',
    name: 'ДЕМО флоатер',
    instrumentType: 'bond',
    currency: 'RUB',
    nominal: '1000',
    pricePct: '100.0',
    couponType: 'floating',
    couponFrequency: 4,
    dataAsOf: '2026-10-20',
    provider: 'cbonds',
  },
  {
    isin: 'XS0191754729',
    name: 'ДЕМО еврооблигация (USD)',
    instrumentType: 'bond',
    currency: 'USD',
    nominal: '1000',
    pricePct: '98.5',
    couponType: 'fixed',
    couponFrequency: 2,
    dataAsOf: '2026-10-20',
    provider: 'cbonds',
  },
  {
    isin: 'RU000A0JX0J2',
    name: 'ДЕМО выпуск без дюрации',
    instrumentType: 'bond',
    currency: 'RUB',
    nominal: '1000',
    pricePct: '101.2',
    couponType: 'fixed',
    couponFrequency: 2,
    dataAsOf: '2026-10-20',
    provider: 'cbonds',
  },
];

let mockRevision = 0;
let mockTrades: Trade[] = [];
let mockWorkspaceId = 'mock-workspace-001';
let mockLastCalcId: string | null = null;
const mockCalculations = new Map<string, CalculationResult>();

function findMockInstrument(isin: string): Instrument | null {
  const norm = isin.trim().toUpperCase();
  return mockInstruments.find((i) => i.isin.toUpperCase() === norm) ?? null;
}

export function resetMockState(): void {
  mockRevision = 0;
  mockTrades = [];
  mockWorkspaceId = 'mock-workspace-' + generateId().slice(0, 8);
  mockLastCalcId = null;
  mockCalculations.clear();
}

export async function mockOpenWorkspace(): Promise<WorkspaceCreated> {
  return {
    id: mockWorkspaceId,
    revision: mockRevision,
    trades: [...mockTrades],
    lastCalculationId: mockLastCalcId,
    createdAt: new Date().toISOString(),
    updatedAt: new Date().toISOString(),
    csrfToken: 'mock-csrf-token-' + generateId().slice(0, 8),
  };
}

export async function mockGetWorkspace(): Promise<Workspace> {
  return {
    id: mockWorkspaceId,
    revision: mockRevision,
    trades: [...mockTrades],
    lastCalculationId: mockLastCalcId,
    createdAt: new Date().toISOString(),
    updatedAt: new Date().toISOString(),
  };
}

export async function mockFetchInstrument(isin: string): Promise<Instrument | null> {
  return findMockInstrument(isin);
}

export async function mockAddTrade(
  isin: string,
  quantity: number,
  expectedRevision: number,
): Promise<Workspace> {
  if (expectedRevision !== mockRevision) {
    const error: any = new Error('Набор был изменен другим действием');
    error.response = {
      status: 409,
      data: {
        code: 'REVISION_CONFLICT',
        message: 'Набор был изменен. Обновите данные.',
        details: [],
      },
    };
    throw error;
  }

  const qRes = validateQuantity(quantity);
  if (!qRes.valid) {
    const error: any = new Error(qRes.error);
    error.response = {
      status: 422,
      data: {
        code: 'VALIDATION_ERROR',
        message: 'Ошибка валидации',
        details: [{ field: 'body.quantity', message: qRes.error }],
      },
    };
    throw error;
  }

  const normIsin = isin.trim().toUpperCase();
  const inst = findMockInstrument(normIsin);

  const newTrade: Trade = {
    id: generateId(),
    isin: normIsin,
    quantity,
    instrument: inst,
    createdAt: new Date().toISOString(),
    updatedAt: new Date().toISOString(),
  };

  mockTrades.push(newTrade);
  mockRevision += 1;

  return mockGetWorkspace();
}

export async function mockUpdateTrade(
  tradeId: string,
  expectedRevision: number,
  patch: { quantity?: number; isin?: string },
): Promise<Workspace> {
  if (expectedRevision !== mockRevision) {
    const error: any = new Error('Конфликт ревизий');
    error.response = {
      status: 409,
      data: {
        code: 'REVISION_CONFLICT',
        message: 'Конфликт ревизий: набор уже изменён',
        details: [],
      },
    };
    throw error;
  }

  const trade = mockTrades.find((t) => t.id === tradeId);
  if (!trade) {
    const error: any = new Error('Сделка не найдена');
    error.response = {
      status: 404,
      data: { code: 'TRADE_NOT_FOUND', message: 'Сделка не найдена', details: [] },
    };
    throw error;
  }

  if (patch.quantity !== undefined) {
    const qRes = validateQuantity(patch.quantity);
    if (!qRes.valid) {
      const error: any = new Error(qRes.error);
      error.response = {
        status: 422,
        data: {
          code: 'VALIDATION_ERROR',
          message: 'Ошибка валидации',
          details: [{ field: 'body.quantity', message: qRes.error }],
        },
      };
      throw error;
    }
    trade.quantity = patch.quantity;
  }

  if (patch.isin !== undefined) {
    const normIsin = patch.isin.trim().toUpperCase();
    trade.isin = normIsin;
    trade.instrument = findMockInstrument(normIsin);
  }

  trade.updatedAt = new Date().toISOString();
  mockRevision += 1;

  return mockGetWorkspace();
}

export async function mockDeleteTrade(
  tradeId: string,
  expectedRevision: number,
): Promise<Workspace> {
  if (expectedRevision !== mockRevision) {
    const error: any = new Error('Конфликт ревизий');
    error.response = {
      status: 409,
      data: {
        code: 'REVISION_CONFLICT',
        message: 'Конфликт ревизий: набор уже изменён',
        details: [],
      },
    };
    throw error;
  }

  const idx = mockTrades.findIndex((t) => t.id === tradeId);
  if (idx === -1) {
    const error: any = new Error('Сделка не найдена');
    error.response = {
      status: 404,
      data: { code: 'TRADE_NOT_FOUND', message: 'Сделка не найдена', details: [] },
    };
    throw error;
  }

  mockTrades.splice(idx, 1);
  mockRevision += 1;

  return mockGetWorkspace();
}

export async function mockRunCalculation(
  expectedRevision: number,
  _valuationDate?: string,
): Promise<CalculationResult> {
  if (expectedRevision !== mockRevision) {
    const error: any = new Error('Конфликт ревизий при запуске расчёта');
    error.response = {
      status: 409,
      data: {
        code: 'REVISION_CONFLICT',
        message: 'Набор был изменен во время расчёта',
        details: [],
      },
    };
    throw error;
  }

  if (mockTrades.length === 0) {
    const error: any = new Error('Набор сделок пуст');
    error.response = {
      status: 422,
      data: {
        code: 'WORKSPACE_EMPTY',
        message: 'Нельзя запустить расчёт для пустого набора',
        details: [],
      },
    };
    throw error;
  }

  const BP = 0.0001;
  const rows: CalculationRow[] = [];
  let totalMetric = 0;
  let hasFailed = false;

  for (const t of mockTrades) {
    const inst = findMockInstrument(t.isin);
    if (!inst || !inst.nominal || !inst.pricePct) {
      rows.push({
        tradeId: t.id,
        isin: t.isin,
        quantity: t.quantity,
        status: 'failed',
        errorCode: 'MARKET_DATA_MISSING',
        provider: null,
        dataAsOf: null,
        nominalUsed: null,
        pricePctUsed: null,
        fxRateUsed: null,
        currentFairValue: null,
        durationUsed: null,
        convexityUsed: null,
        metricValue: null,
      });
      hasFailed = true;
      continue;
    }

    const nom = parseFloat(inst.nominal);
    const price = parseFloat(inst.pricePct);
    const fx = inst.currency === 'USD' ? 92.5 : 1.0;
    const cfv = t.quantity * nom * (price / 100) * fx;

    let d = 3.0;
    let c = 12.0;
    if (inst.couponType === 'floating' && inst.couponFrequency) {
      d = 1 / inst.couponFrequency;
      c = 0.0;
    }

    const dv01 = cfv * (-d * BP + 0.5 * c * BP * BP);
    totalMetric += dv01;

    rows.push({
      tradeId: t.id,
      isin: t.isin,
      quantity: t.quantity,
      status: 'success',
      errorCode: null,
      provider: inst.provider,
      dataAsOf: inst.dataAsOf,
      nominalUsed: inst.nominal,
      pricePctUsed: inst.pricePct,
      fxRateUsed: fx.toFixed(4),
      currentFairValue: cfv.toFixed(2),
      durationUsed: d.toFixed(4),
      convexityUsed: c.toFixed(4),
      metricValue: dv01.toFixed(4),
    });
  }

  const calcId = 'calc-' + generateId().slice(0, 8);
  mockLastCalcId = calcId;

  const result: CalculationResult = {
    id: calcId,
    workspaceId: mockWorkspaceId,
    workspaceRevision: mockRevision,
    measureCode: 'DV01',
    methodologyVersion: 'v1.0',
    valuationDate: new Date().toISOString().slice(0, 10),
    status: hasFailed ? (rows.some((r) => r.status === 'success') ? 'partial' : 'failed') : 'success',
    totalValue: totalMetric.toFixed(4),
    currency: 'RUB',
    results: rows,
    createdAt: new Date().toISOString(),
    completedAt: new Date().toISOString(),
  };

  mockCalculations.set(calcId, result);
  return result;
}

export async function mockGetCalculation(calcId: string): Promise<CalculationResult> {
  const found = mockCalculations.get(calcId);
  if (!found) {
    const error: any = new Error('Расчёт не найден');
    error.response = {
      status: 404,
      data: { code: 'CALCULATION_NOT_FOUND', message: 'Расчёт не найден', details: [] },
    };
    throw error;
  }
  return found;
}
