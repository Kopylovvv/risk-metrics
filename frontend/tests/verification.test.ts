import assert from 'node:assert/strict';
import test from 'node:test';
import { validateIsin, validateQuantity } from '../src/utils/helpers.ts';
import {
  mockOpenWorkspace,
  mockAddTrade,
  mockUpdateTrade,
  mockDeleteTrade,
  mockRunCalculation,
  resetMockState,
} from '../src/mocks/data.ts';

test('ISIN и количество: нормализация и ошибки ввода', () => {
  const validIsins = ['RU000A1038V6', 'US0378331005', 'XS0191754729', 'RU000A105A95', 'RU000A0JX0J2'];
  for (const isin of validIsins) {
    const res = validateIsin(isin);
    assert.ok(res.valid === true, `ISIN ${isin} should be valid`);
  }

  const normRes = validateIsin('   ru000a1038v6   ');
  assert.ok(normRes.valid === true, 'Normalized ISIN should be valid');
  assert.ok(normRes.normalized === 'RU000A1038V6', 'Should normalize to uppercase and trim');

  const badCheckDigit = validateIsin('RU000A1038V7');
  assert.ok(badCheckDigit.valid === false, 'Invalid check digit must fail');
  assert.ok(badCheckDigit.error === 'ISIN: неверная контрольная цифра', 'Error message matches rule');

  const badLength = validateIsin('RU000A10');
  assert.ok(badLength.valid === false, 'Short ISIN must fail');
  const emptyIsin = validateIsin('');
  assert.ok(emptyIsin.valid === false, 'Empty ISIN must fail');

  assert.ok(validateQuantity(100).valid === true, '100 should be valid');
  assert.ok(validateQuantity(-250).valid === true, '-250 (short) should be valid');
  assert.ok(validateQuantity('50').value === 50, 'String 50 parsed to number');

  assert.ok(validateQuantity(0).valid === false, '0 should fail');
  assert.ok(validateQuantity(0).error === 'Количество бумаг не может быть 0', 'Zero error message');
  assert.ok(validateQuantity(1.5).valid === false, 'Decimal quantity should fail');
  assert.ok(validateQuantity(1.5).error === 'Количество бумаг должно быть целым числом', 'Integer error message');
  assert.ok(validateQuantity('').valid === false, 'Empty quantity should fail');
  assert.ok(validateQuantity(10 ** 15 + 100).valid === false, 'Exceeding 10^15 limit should fail');

});

test('Набор: создание, добавление, изменение, удаление, revision и расчёт', async () => {
  resetMockState();

  const ws0 = await mockOpenWorkspace();
  assert.ok(ws0.revision === 0, 'Initial revision must be 0');
  assert.ok(ws0.trades.length === 0, 'Initial trades must be empty');
  assert.ok(Boolean(ws0.csrfToken), 'CSRF token must be present');

  const ws1 = await mockAddTrade('RU000A1038V6', 100, 0);
  assert.ok(ws1.revision === 1, 'Revision should be 1');
  assert.ok(ws1.trades.length === 1, 'Trades count should be 1');
  assert.ok(ws1.trades[0].isin === 'RU000A1038V6', 'ISIN should match');
  assert.ok(ws1.trades[0].quantity === 100, 'Quantity should be 100');
  assert.ok(ws1.trades[0].instrument?.name === 'ДЕМО ОФЗ, фиксированный купон', 'Instrument linked');

  const ws2 = await mockAddTrade('RU000A105A95', 200, 1);
  assert.ok(ws2.revision === 2, 'Revision should be 2');
  assert.ok(ws2.trades.length === 2, 'Trades count should be 2');
  assert.ok(ws2.trades[0].isin === 'RU000A1038V6' && ws2.trades[1].isin === 'RU000A105A95', 'Order preserved');

  const tradeId = ws2.trades[0].id;
  const ws3 = await mockUpdateTrade(tradeId, 2, { quantity: -150 });
  assert.ok(ws3.revision === 3, 'Revision should be 3');
  assert.ok(ws3.trades[0].quantity === -150, 'Quantity updated to short -150');

  const ws4 = await mockDeleteTrade(tradeId, 3);
  assert.ok(ws4.revision === 4, 'Revision should be 4');
  assert.ok(ws4.trades.length === 1, 'Trades count should be 1');
  assert.ok(ws4.trades[0].isin === 'RU000A105A95', 'Remaining trade is correct');

  let conflictCaught = false;
  try {
    await mockAddTrade('RU000A1038V6', 50, 0);
  } catch (err: any) {
    conflictCaught = true;
    assert.ok(err.response?.status === 409, 'Conflict status must be 409');
    assert.ok(err.response?.data?.code === 'REVISION_CONFLICT', 'Code must be REVISION_CONFLICT');
  }
  assert.ok(conflictCaught, 'Revision conflict must throw error');

  const calc = await mockRunCalculation(4);
  assert.ok(calc.status === 'success', 'Calculation status must be success');
  assert.ok(calc.results.length === 1, '1 result row');
  assert.ok(calc.totalValue !== null, 'Total value calculated');

});

test('При отсутствии рыночных данных общий итог не подменяется числом', async () => {
  resetMockState();
  await mockOpenWorkspace();
  await mockAddTrade('RU000A1038V6', 1, 0);
  await mockAddTrade('US0378331005', 1, 1);
  const partial = await mockRunCalculation(2);
  assert.equal(partial.status, 'partial');
  assert.equal(partial.totalValue, null);
  assert.equal(partial.results[1].metricValue, null);
  await mockDeleteTrade(partial.results[0].tradeId!, 2);
  const failed = await mockRunCalculation(3);
  assert.equal(failed.status, 'failed');
  assert.equal(failed.totalValue, null);
});
