export interface Instrument {
  isin: string;
  name: string | null;
  instrumentType: string;
  currency: string;
  nominal: string | null;
  pricePct: string | null;
  accruedInterest: string | null;
  couponType: 'fixed' | 'floating' | 'zero' | null;
  couponFrequency: number | null;
  dataAsOf: string | null;
  provider: string | null;
}

export interface Trade {
  id: string;
  isin: string;
  quantity: number;
  instrument: Instrument | null;
  createdAt: string;
  updatedAt: string;
}

export interface Workspace {
  id: string;
  revision: number;
  trades: Trade[];
  lastCalculationId: string | null;
  createdAt: string;
  updatedAt: string;
}

export interface WorkspaceCreated extends Workspace {
  csrfToken: string;
}

export interface CalculationRow {
  tradeId: string | null;
  isin: string;
  quantity: number;
  status: 'success' | 'failed';
  errorCode: string | null;
  provider: string | null;
  dataAsOf: string | null;
  nominalUsed: string | null;
  pricePctUsed: string | null;
  accruedInterestUsed: string | null;
  fxRateUsed: string | null;
  currentFairValue: string | null;
  durationUsed: string | null;
  convexityUsed: string | null;
  metricValue: string | null;
}

export interface CalculationResult {
  id: string;
  workspaceId: string;
  workspaceRevision: number;
  measureCode: string;
  methodologyVersion: string;
  valuationDate: string;
  status: 'success' | 'partial' | 'failed';
  totalValue: string | null;
  currency: string;
  results: CalculationRow[];
  createdAt: string;
  completedAt: string | null;
}

export interface ApiErrorDetail {
  field: string | null;
  message: string;
}

export interface ApiError {
  status: number;
  code: string;
  message: string;
  details: ApiErrorDetail[];
}
