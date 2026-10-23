import { validateIsin, validateQuantity } from '../utils/helpers.ts';
import {
  mockOpenWorkspace,
  mockAddTrade,
  mockUpdateTrade,
  mockDeleteTrade,
  mockRunCalculation,
  resetMockState,
} from '../mocks/data.ts';

async function runTests() {
  const validIsins = ['RU000A1038V6', 'US0378331005', 'XS0191754729', 'RU000A105A95', 'RU000A0JX0J2'];
  for (const isin of validIsins) {
    const res = validateIsin(isin);
    console.assert(res.valid === true, `ISIN ${isin} should be valid`);
  }

  const normRes = validateIsin('   ru000a1038v6   ');
  console.assert(normRes.valid === true, 'Normalized ISIN should be valid');
  console.assert(normRes.normalized === 'RU000A1038V6', 'Should normalize to uppercase and trim');

  const badCheckDigit = validateIsin('RU000A1038V7');
  console.assert(badCheckDigit.valid === false, 'Invalid check digit must fail');
  console.assert(badCheckDigit.error === 'ISIN: неверная контрольная цифра', 'Error message matches rule');

  const badLength = validateIsin('RU000A10');
  console.assert(badLength.valid === false, 'Short ISIN must fail');
  const emptyIsin = validateIsin('');
  console.assert(emptyIsin.valid === false, 'Empty ISIN must fail');

  console.assert(validateQuantity(100).valid === true, '100 should be valid');
  console.assert(validateQuantity(-250).valid === true, '-250 (short) should be valid');
  console.assert(validateQuantity('50').value === 50, 'String 50 parsed to number');

  console.assert(validateQuantity(0).valid === false, '0 should fail');
  console.assert(validateQuantity(0).error === 'Количество бумаг не может быть 0', 'Zero error message');
  console.assert(validateQuantity(1.5).valid === false, 'Decimal quantity should fail');
  console.assert(validateQuantity(1.5).error === 'Количество бумаг должно быть целым числом', 'Integer error message');
  console.assert(validateQuantity('').valid === false, 'Empty quantity should fail');
  console.assert(validateQuantity(10 ** 15 + 100).valid === false, 'Exceeding 10^15 limit should fail');

  resetMockState();

  const ws0 = await mockOpenWorkspace();
  console.assert(ws0.revision === 0, 'Initial revision must be 0');
  console.assert(ws0.trades.length === 0, 'Initial trades must be empty');
  console.assert(Boolean(ws0.csrfToken), 'CSRF token must be present');

  const ws1 = await mockAddTrade('RU000A1038V6', 100, 0);
  console.assert(ws1.revision === 1, 'Revision should be 1');
  console.assert(ws1.trades.length === 1, 'Trades count should be 1');
  console.assert(ws1.trades[0].isin === 'RU000A1038V6', 'ISIN should match');
  console.assert(ws1.trades[0].quantity === 100, 'Quantity should be 100');
  console.assert(ws1.trades[0].instrument?.name === 'ДЕМО ОФЗ, фиксированный купон', 'Instrument linked');

  const ws2 = await mockAddTrade('RU000A105A95', 200, 1);
  console.assert(ws2.revision === 2, 'Revision should be 2');
  console.assert(ws2.trades.length === 2, 'Trades count should be 2');
  console.assert(ws2.trades[0].isin === 'RU000A1038V6' && ws2.trades[1].isin === 'RU000A105A95', 'Order preserved');

  const tradeId = ws2.trades[0].id;
  const ws3 = await mockUpdateTrade(tradeId, 2, { quantity: -150 });
  console.assert(ws3.revision === 3, 'Revision should be 3');
  console.assert(ws3.trades[0].quantity === -150, 'Quantity updated to short -150');

  const ws4 = await mockDeleteTrade(tradeId, 3);
  console.assert(ws4.revision === 4, 'Revision should be 4');
  console.assert(ws4.trades.length === 1, 'Trades count should be 1');
  console.assert(ws4.trades[0].isin === 'RU000A105A95', 'Remaining trade is correct');

  let conflictCaught = false;
  try {
    await mockAddTrade('RU000A1038V6', 50, 0);
  } catch (err: any) {
    conflictCaught = true;
    console.assert(err.response?.status === 409, 'Conflict status must be 409');
    console.assert(err.response?.data?.code === 'REVISION_CONFLICT', 'Code must be REVISION_CONFLICT');
  }
  console.assert(conflictCaught, 'Revision conflict must throw error');

  const calc = await mockRunCalculation(4);
  console.assert(calc.status === 'success', 'Calculation status must be success');
  console.assert(calc.results.length === 1, '1 result row');
  console.assert(calc.totalValue !== null, 'Total value calculated');

  console.log('All verification tests passed successfully.');
}

runTests().catch((e) => {
  console.error('Test failed:', e);
  throw e;
});

