import type { Trade, Instrument } from '@/types/api';

interface TradeListProps {
  trades: Trade[];
  instruments: Map<string, Instrument>;
  onRemove: (id: string) => void;
  onUpdate: (id: string, positionSize: number) => void;
}

export default function TradeList({
  trades,
  instruments,
  onRemove,
  onUpdate,
}: TradeListProps) {
  if (trades.length === 0) {
    return (
      <div id="empty-trades" className="text-center py-12">
        <div className="w-16 h-16 mx-auto mb-4 rounded-2xl bg-[var(--color-surface)] border border-[var(--color-border)] flex items-center justify-center">
          <svg className="w-7 h-7 text-[var(--color-text-muted)]" fill="none" viewBox="0 0 24 24" strokeWidth={1.5} stroke="currentColor">
            <path strokeLinecap="round" strokeLinejoin="round" d="M19.5 14.25v-2.625a3.375 3.375 0 0 0-3.375-3.375h-1.5A1.125 1.125 0 0 1 13.5 7.125v-1.5a3.375 3.375 0 0 0-3.375-3.375H8.25m3.75 9v6m3-3H9m1.5-12H5.625c-.621 0-1.125.504-1.125 1.125v17.25c0 .621.504 1.125 1.125 1.125h12.75c.621 0 1.125-.504 1.125-1.125V11.25a9 9 0 0 0-9-9Z" />
          </svg>
        </div>
        <p className="text-sm text-[var(--color-text-muted)]">
          Нет добавленных сделок
        </p>
        <p className="text-xs text-[var(--color-text-muted)] mt-1">
          Добавьте сделку через форму слева
        </p>
      </div>
    );
  }

  return (
    <div className="space-y-3">
      <div className="flex items-center justify-between">
        <h2 className="text-base font-semibold text-[var(--color-text)]">
          Сделки
        </h2>
        <span className="text-xs text-[var(--color-text-muted)] bg-[var(--color-surface-hover)] px-2 py-0.5 rounded-full">
          {trades.length}
        </span>
      </div>

      <ul id="trade-list" className="space-y-2">
        {trades.map((trade) => {
          const inst = instruments.get(trade.isin);
          return (
            <li
              key={trade.id}
              className="group rounded-lg border border-[var(--color-border)] bg-[var(--color-bg)] p-4 hover:border-[var(--color-border-focus)]/30 transition-colors duration-150"
            >
              <div className="flex items-start justify-between gap-4">
                <div className="min-w-0 flex-1">
                  <p className="text-sm font-medium text-[var(--color-text)] truncate">
                    {inst?.name ?? trade.isin}
                  </p>
                  <p className="text-xs text-[var(--color-text-muted)] mt-0.5 font-mono">
                    {trade.isin}
                    {inst && (
                      <span className="ml-2 text-[var(--color-text-secondary)]">
                        · {inst.currency} · {inst.couponType === 'fixed' ? 'Фикс.' : 'Плав.'} купон
                      </span>
                    )}
                  </p>
                </div>

                <button
                  id={`remove-trade-${trade.id}`}
                  onClick={() => onRemove(trade.id)}
                  className="
                    shrink-0 p-1.5 rounded-md text-[var(--color-text-muted)]
                    hover:text-[var(--color-danger)] hover:bg-[var(--color-danger)]/10
                    transition-colors duration-150 cursor-pointer
                  "
                  title="Удалить сделку"
                >
                  <svg className="w-4 h-4" fill="none" viewBox="0 0 24 24" strokeWidth={1.5} stroke="currentColor">
                    <path strokeLinecap="round" strokeLinejoin="round" d="m14.74 9-.346 9m-4.788 0L9.26 9m9.968-3.21c.342.052.682.107 1.022.166m-1.022-.165L18.16 19.673a2.25 2.25 0 0 1-2.244 2.077H8.084a2.25 2.25 0 0 1-2.244-2.077L4.772 5.79m14.456 0a48.108 48.108 0 0 0-3.478-.397m-12 .562c.34-.059.68-.114 1.022-.165m0 0a48.11 48.11 0 0 1 3.478-.397m7.5 0v-.916c0-1.18-.91-2.164-2.09-2.201a51.964 51.964 0 0 0-3.32 0c-1.18.037-2.09 1.022-2.09 2.201v.916m7.5 0a48.667 48.667 0 0 0-7.5 0" />
                  </svg>
                </button>
              </div>

              {/* Editable position size */}
              <div className="mt-3 flex items-center gap-3">
                <label className="text-xs text-[var(--color-text-secondary)]">Позиция:</label>
                <input
                  type="number"
                  min={1}
                  step={1}
                  value={trade.positionSize}
                  onChange={(e) => {
                    const val = Number(e.target.value);
                    if (val > 0) onUpdate(trade.id, val);
                  }}
                  className="
                    w-28 rounded-md border border-[var(--color-border)] bg-[var(--color-surface)] px-3 py-1.5 text-sm
                    focus:outline-none focus:ring-2 focus:ring-[var(--color-border-focus)]/40 focus:border-[var(--color-border-focus)]
                    transition-colors duration-150
                  "
                />
              </div>
            </li>
          );
        })}
      </ul>
    </div>
  );
}
