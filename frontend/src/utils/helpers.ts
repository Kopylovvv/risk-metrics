export function generateId(): string {
  return crypto.randomUUID();
}

export function isinCheckDigitOk(isin: string): boolean {
  if (isin.length !== 12) return false;
  const upper = isin.toUpperCase();
  let digits = '';
  for (let i = 0; i < 11; i++) {
    const code = upper.charCodeAt(i);
    if (code >= 48 && code <= 57) {
      digits += upper[i];
    } else if (code >= 65 && code <= 90) {
      digits += (code - 55).toString();
    } else {
      return false;
    }
  }

  let total = 0;
  for (let i = 0; i < digits.length; i++) {
    let d = parseInt(digits[digits.length - 1 - i], 10);
    if (i % 2 === 0) {
      d *= 2;
      if (d > 9) d -= 9;
    }
    total += d;
  }
  const expectedCheckDigit = (10 - (total % 10)) % 10;
  return expectedCheckDigit === parseInt(upper[11], 10);
}

export function validateIsin(raw: string): { valid: boolean; error?: string; normalized: string } {
  const normalized = (raw || '').trim().toUpperCase();
  if (!normalized) {
    return { valid: false, error: 'Введите ISIN инструмента', normalized };
  }
  if (!/^[A-Z]{2}[A-Z0-9]{9}[0-9]$/.test(normalized)) {
    return {
      valid: false,
      error: 'ISIN: 12 символов (2 буквы страны, 9 букв или цифр, контрольная цифра)',
      normalized,
    };
  }
  if (!isinCheckDigitOk(normalized)) {
    return {
      valid: false,
      error: 'ISIN: неверная контрольная цифра',
      normalized,
    };
  }
  return { valid: true, normalized };
}

export function isValidIsin(isin: string): boolean {
  return validateIsin(isin).valid;
}

export const QUANTITY_LIMIT = 10 ** 15;

export function validateQuantity(raw: string | number): {
  valid: boolean;
  error?: string;
  value?: number;
} {
  if (raw === '' || raw === null || raw === undefined) {
    return { valid: false, error: 'Укажите количество бумаг' };
  }
  const val = typeof raw === 'number' ? raw : Number(raw);
  if (isNaN(val)) {
    return { valid: false, error: 'Укажите корректное число' };
  }
  if (!Number.isInteger(val)) {
    return { valid: false, error: 'Количество бумаг должно быть целым числом' };
  }
  if (val === 0) {
    return { valid: false, error: 'Количество бумаг не может быть 0' };
  }
  if (Math.abs(val) > QUANTITY_LIMIT) {
    return { valid: false, error: 'Количество бумаг не более 10¹⁵' };
  }
  return { valid: true, value: val };
}

// Коды ошибок строки расчёта (RowErrorCode в backend): короткая метка и пояснение
const ROW_ERRORS: Record<string, { label: string; hint: string }> = {
  INSTRUMENT_NOT_FOUND: { label: 'Нет выпуска', hint: 'Источник не знает такой ISIN' },
  MISSING_MARKET_DATA: {
    label: 'Нет данных',
    hint: 'У источника нет цены, НКД, дюрации или выпуклости на дату оценки',
  },
  INCOMPATIBLE_MARKET_DATA: {
    label: 'Ошибка данных',
    hint: 'Источник вернул несовместимые данные (валюта, единицы или невозможные значения)',
  },
  PROVIDER_UNAVAILABLE: {
    label: 'Источник недоступен',
    hint: 'Источник рыночных данных не ответил, сохранённых данных нет. Повторите позже',
  },
  MISSING_FX_RATE: { label: 'Нет курса', hint: 'ЦБ не устанавливал курс этой валюты на дату' },
  FX_PROVIDER_UNAVAILABLE: {
    label: 'Курсы недоступны',
    hint: 'Источник курсов валют не ответил, сохранённого курса нет. Повторите позже',
  },
};

export function rowErrorText(code: string | null): { label: string; hint: string } {
  return (code && ROW_ERRORS[code]) || { label: 'Ошибка', hint: code ?? 'Ошибка расчёта строки' };
}

// Предупреждение о запасных данных: источник не ответил, расчёт взял сохранённые
export function fallbackNote(row: {
  marketDataFallback: boolean | null;
  fxRateFallback: boolean | null;
  dataAsOf: string | null;
  fxRateDate: string | null;
}): string | null {
  const quote = row.marketDataFallback ? `котировка от ${row.dataAsOf ?? '?'}` : null;
  const rate = row.fxRateFallback ? `курс от ${row.fxRateDate ?? '?'}` : null;
  if (!quote && !rate) return null;
  const what = [quote, rate].filter(Boolean).join(' и ');
  return `Источник не ответил: использованы сохранённые данные (${what})`;
}

// Результат устарел, если набор изменился после расчёта (revision другой)
export function isResultStale(
  workspace: { revision: number } | null,
  result: { workspaceRevision: number } | null,
): boolean {
  return workspace !== null && result !== null && workspace.revision !== result.workspaceRevision;
}

export function formatNumber(value: number, digits = 4): string {
  return value.toLocaleString('ru-RU', {
    minimumFractionDigits: digits,
    maximumFractionDigits: digits,
  });
}
