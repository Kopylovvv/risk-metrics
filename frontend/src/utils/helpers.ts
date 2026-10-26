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

export function formatNumber(value: number, digits = 4): string {
  return value.toLocaleString('ru-RU', {
    minimumFractionDigits: digits,
    maximumFractionDigits: digits,
  });
}
