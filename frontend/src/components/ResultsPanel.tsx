import type { CalculationResult } from '@/types/api';

interface ResultsPanelProps {
  result: CalculationResult | null;
  loading: boolean;
  isStale: boolean;
  timestamp: string | null;
}

export default function ResultsPanel({
  result,
  loading,
  isStale,
  timestamp,
}: ResultsPanelProps) {
  const cfvTotal = result
    ? result.results
        .filter((r) => r.status === 'success' && r.currentFairValue)
        .reduce((sum, r) => sum + Number(r.currentFairValue), 0)
    : 0;

  const totalDv01 = result?.totalValue ? Number(result.totalValue) : null;

  const failedCount = result
    ? result.results.filter((r) => r.status === 'failed').length
    : 0;

  const statusLabel =
    result?.status === 'success'
      ? 'Полный расчёт'
      : result?.status === 'partial'
        ? 'Частичный расчёт'
        : result?.status === 'failed'
          ? 'Ошибка расчёта'
          : '';

  const statusColor =
    result?.status === 'success'
      ? 'text-emerald-400'
      : result?.status === 'partial'
        ? 'text-amber-400'
        : 'text-red-400';

  return (
    <div className="space-y-3">
      <div className="rounded-xl border border-[#1a2438] bg-[#0c121e] p-5 shadow-sm min-h-[290px] flex flex-col justify-between">
        <div>
          <div className="flex items-center justify-between mb-6 pb-3 border-b border-[#162033]">
            <div className="flex items-center gap-2">
              <svg
                className="w-4 h-4 text-slate-400"
                viewBox="0 0 24 24"
                fill="none"
                stroke="currentColor"
                strokeWidth="2"
                strokeLinecap="round"
                strokeLinejoin="round"
              >
                <path d="M3 3v18h18" />
                <path d="m19 9-5 5-4-4-3 3" />
              </svg>
              <span className="text-[12px] font-semibold uppercase tracking-wider text-slate-400">
                Результаты расчёта: влияние на портфель
              </span>
            </div>
            {timestamp && (
              <div className="flex items-center gap-2">
                {isStale && (
                  <span
                    id="stale-badge"
                    className="font-mono text-[10px] uppercase font-bold tracking-wider px-2 py-0.5 rounded bg-amber-500/20 text-amber-300 border border-amber-500/40"
                  >
                    Результат устарел
                  </span>
                )}
                <span className="font-mono text-[12px] text-slate-400">
                  {timestamp}
                </span>
              </div>
            )}
          </div>

          {loading ? (
            <div id="calc-loading" className="py-12 text-center">
              <div className="w-8 h-8 mx-auto mb-3 border-2 border-blue-500 border-t-transparent rounded-full animate-spin" />
              <p className="text-sm text-slate-300 font-medium">Выполняется расчёт DV01 по набору сделок…</p>
              <p className="text-xs text-slate-500 mt-1">Оценка рыночного риска портфеля</p>
            </div>
          ) : result ? (
            <div className="space-y-4">
              <div className="flex items-center gap-2">
                <span className={`text-[12px] font-semibold ${statusColor}`}>
                  {statusLabel}
                </span>
                {failedCount > 0 && (
                  <span className="text-[11px] text-slate-400">
                    ({failedCount} {failedCount === 1 ? 'сделка' : failedCount < 5 ? 'сделки' : 'сделок'} без рыночных данных)
                  </span>
                )}
              </div>

              {totalDv01 !== null ? (
                <div>
                  <p className="text-[13px] text-slate-300 mb-1">
                    Риск-метрика DV01 (изменение стоимости при сдвиге кривой доходности на 1 bp)
                  </p>
                  <div className="flex items-baseline gap-2">
                    <span
                      id="total-dv01"
                      className={`font-mono text-4xl font-bold tracking-tight ${
                        totalDv01 < 0 ? 'text-red-500' : 'text-emerald-400'
                      }`}
                    >
                      {totalDv01 > 0 ? `+${totalDv01.toFixed(4)}` : totalDv01.toFixed(4)}
                    </span>
                    <span
                      className={`font-mono text-2xl font-bold ${
                        totalDv01 < 0 ? 'text-red-500' : 'text-emerald-400'
                      }`}
                    >
                      {result.currency}
                    </span>
                  </div>
                </div>
              ) : (
                <div>
                  <p className="text-[13px] text-slate-300 mb-1">
                    Риск-метрика DV01
                  </p>
                  <p className="text-sm text-amber-400 font-medium">
                    Не удалось рассчитать суммарный итог — отсутствуют рыночные данные по отдельным бумагам
                  </p>
                </div>
              )}

              {isStale && (
                <div
                  id="portfolio-stale-warning"
                  className="flex items-start gap-2 text-[12px] text-amber-300 bg-amber-950/40 border border-amber-600/40 rounded-lg p-3"
                >
                  <svg
                    className="w-4 h-4 shrink-0 text-amber-400 mt-0.5"
                    viewBox="0 0 24 24"
                    fill="none"
                    stroke="currentColor"
                    strokeWidth="2"
                    strokeLinecap="round"
                    strokeLinejoin="round"
                  >
                    <path d="m21.73 18-8-14a2 2 0 0 0-3.48 0l-8 14A2 2 0 0 0 4 21h16a2 2 0 0 0 1.73-3Z" />
                    <line x1="12" y1="9" x2="12" y2="13" />
                    <line x1="12" y1="17" x2="12.01" y2="17" />
                  </svg>
                  <div>
                    <span className="font-semibold">Состав или параметры портфеля изменены!</span>
                    <p className="text-[11px] text-amber-400/80 mt-0.5">
                      Отображаются результаты предыдущего расчёта. Нажмите «Запустить расчёт риск-метрики» для актуализации.
                    </p>
                  </div>
                </div>
              )}

              {result.results.length > 0 && (
                <div className="mt-4 rounded-lg border border-[#162033] bg-[#070b13] overflow-hidden">
                  <table className="w-full text-[12px]">
                    <thead>
                      <tr className="border-b border-[#162033] text-slate-400">
                        <th className="text-left px-3 py-2 font-medium">ISIN</th>
                        <th className="text-right px-3 py-2 font-medium">Кол-во</th>
                        <th className="text-right px-3 py-2 font-medium">CFV ({result.currency})</th>
                        <th className="text-right px-3 py-2 font-medium">DV01 ({result.currency})</th>
                        <th className="text-center px-3 py-2 font-medium">Статус</th>
                      </tr>
                    </thead>
                    <tbody className="divide-y divide-[#141b29]">
                      {result.results.map((row, idx) => (
                        <tr key={row.tradeId ?? idx} className="hover:bg-[#0c1220]">
                          <td className="px-3 py-2 font-mono text-slate-200">
                            <span>{row.isin}</span>
                            {row.dataAsOf && (
                              <span className="block text-[10px] text-slate-500 font-mono">
                                от {row.dataAsOf}
                              </span>
                            )}
                          </td>
                          <td className={`px-3 py-2 text-right font-mono ${row.quantity < 0 ? 'text-red-400' : 'text-slate-200'}`}>
                            {row.quantity.toLocaleString('ru-RU')}
                          </td>
                          <td className="px-3 py-2 text-right font-mono text-slate-200">
                            {row.currentFairValue
                              ? Number(row.currentFairValue).toLocaleString('ru-RU', { maximumFractionDigits: 2 })
                              : <span className="text-slate-500">—</span>}
                          </td>
                          <td className={`px-3 py-2 text-right font-mono font-medium ${
                            row.metricValue ? (Number(row.metricValue) < 0 ? 'text-red-400' : 'text-emerald-400') : 'text-slate-500'
                          }`}>
                            {row.metricValue
                              ? Number(row.metricValue).toLocaleString('ru-RU', { maximumFractionDigits: 4 })
                              : <span className="text-slate-500">—</span>}
                          </td>
                          <td className="px-3 py-2 text-center">
                            {row.status === 'success' ? (
                              <span className="inline-flex items-center gap-1 text-[11px] text-emerald-400 font-medium bg-emerald-500/10 px-2 py-0.5 rounded border border-emerald-500/20">
                                ✓ Ок
                              </span>
                            ) : (
                              <span
                                className="inline-flex items-center gap-1 text-[11px] text-red-400 font-medium bg-red-500/10 px-2 py-0.5 rounded border border-red-500/20"
                                title={row.errorCode ?? 'Ошибка данных'}
                              >
                                ✗ {row.errorCode ? 'Нет данных' : 'Ошибка'}
                              </span>
                            )}
                          </td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
              )}

              {cfvTotal > 0 && (
                <div className="mt-4 rounded-lg border border-[#162033] bg-[#070b13] p-4">
                  <span className="block text-[12px] text-slate-400 mb-1.5">
                    Справедливая стоимость позиции (CurrentFairValue)
                  </span>
                  <span className="font-mono text-[22px] font-bold text-white tracking-wide">
                    {cfvTotal.toLocaleString('ru-RU', { minimumFractionDigits: 2, maximumFractionDigits: 2 })} {result.currency}
                  </span>
                </div>
              )}
            </div>
          ) : (
            <div className="py-12 text-center text-sm text-slate-500">
              Добавьте сделки в портфель и нажмите «Запустить расчёт риск-метрики»
            </div>
          )}
        </div>
      </div>

      <p className="text-[11px] font-mono text-slate-500 tracking-tight px-1">
        Методология: CurrentFairValue × (-DurationMod × 1bp + 1/2 × Convexity × 1bp²)
      </p>
    </div>
  );
}
