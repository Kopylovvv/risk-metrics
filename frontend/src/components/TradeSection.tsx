import { useState } from 'react';
import type { Trade, Instrument } from '@/types/api';
import { fetchInstrument, getErrorMessage, extractApiError } from '@/api/client';
import { validateIsin, validateQuantity, formatNumber } from '@/utils/helpers';

interface TradeSectionProps {
  trades: Trade[];
  selectedInstrument: Instrument | null;
  onSelectInstrument: (inst: Instrument | null) => void;
  onAddTrade: (isin: string, quantity: number) => Promise<void>;
  onRemoveTrade: (id: string) => Promise<void>;
  onUpdateTrade: (id: string, quantity: number) => Promise<void>;
  onCalculate: () => void;
  calculating: boolean;
  calcError: string | null;
  mutating: boolean;
  mutError: string | null;
}

export default function TradeSection({
  trades,
  selectedInstrument: _selectedInstrument,
  onSelectInstrument,
  onAddTrade,
  onRemoveTrade,
  onUpdateTrade,
  onCalculate,
  calculating,
  calcError,
  mutating,
  mutError,
}: TradeSectionProps) {
  const [isin, setIsin] = useState('');
  const [quantity, setQuantity] = useState('');

  const [_currentInstrument, setCurrentInstrument] = useState<Instrument | null>(null);
  const [loadingInstrument, setLoadingInstrument] = useState(false);

  const [isinError, setIsinError] = useState<string | null>(null);
  const [quantityError, setQuantityError] = useState<string | null>(null);

  const [editingTradeId, setEditingTradeId] = useState<string | null>(null);
  const [editQuantityValue, setEditQuantityValue] = useState<string>('');
  const [editError, setEditError] = useState<string | null>(null);

  async function resolveInstrument(code: string): Promise<Instrument | null> {
    const vRes = validateIsin(code);
    if (!vRes.valid) {
      setIsinError(vRes.error || 'Неверный ISIN');
      setCurrentInstrument(null);
      return null;
    }

    setLoadingInstrument(true);
    setIsinError(null);
    try {
      const found = await fetchInstrument(vRes.normalized);
      if (found) {
        setCurrentInstrument(found);
        onSelectInstrument(found);
      } else {
        setCurrentInstrument(null);
      }
      return found;
    } catch (err) {
      setIsinError(getErrorMessage(err));
      setCurrentInstrument(null);
      return null;
    } finally {
      setLoadingInstrument(false);
    }
  }

  function handleIsinBlur() {
    if (isin.trim()) {
      resolveInstrument(isin);
    }
  }

  async function handleAddSubmit(e: React.FormEvent) {
    e.preventDefault();

    const isinRes = validateIsin(isin);
    if (!isinRes.valid) {
      setIsinError(isinRes.error || 'Неверный ISIN');
      return;
    }

    const qtyRes = validateQuantity(quantity);
    if (!qtyRes.valid || qtyRes.value === undefined) {
      setQuantityError(qtyRes.error || 'Неверное количество бумаг');
      return;
    }

    setQuantityError(null);
    setIsinError(null);

    try {
      await onAddTrade(isinRes.normalized, qtyRes.value);
      setQuantity('');
      setQuantityError(null);
      setIsinError(null);
    } catch (err) {
      const apiErr = extractApiError(err);
      if (apiErr?.details && apiErr.details.length > 0) {
        for (const d of apiErr.details) {
          if (d.field?.includes('isin')) setIsinError(d.message);
          if (d.field?.includes('quantity')) setQuantityError(d.message);
        }
      }
    }
  }

  function startEditing(trade: Trade) {
    setEditingTradeId(trade.id);
    setEditQuantityValue(trade.quantity.toString());
    setEditError(null);
    if (trade.instrument) onSelectInstrument(trade.instrument);
  }

  async function saveEditing(id: string) {
    const qRes = validateQuantity(editQuantityValue);
    if (!qRes.valid || qRes.value === undefined) {
      setEditError(qRes.error || 'Неверное количество бумаг');
      return;
    }

    try {
      await onUpdateTrade(id, qRes.value);
      setEditingTradeId(null);
      setEditQuantityValue('');
      setEditError(null);
    } catch (err) {
      const apiErr = extractApiError(err);
      const msg = apiErr?.details?.[0]?.message || getErrorMessage(err);
      setEditError(msg);
    }
  }

  function cancelEditing() {
    setEditingTradeId(null);
    setEditQuantityValue('');
    setEditError(null);
  }

  return (
    <div className="space-y-4">
      <div className="rounded-xl border border-[#1a2438] bg-[#0c121e] p-5 shadow-sm">
        <div className="flex items-center justify-between mb-4 pb-3 border-b border-[#162033]">
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
              <path d="M16 4h2a2 2 0 0 1 2 2v14a2 2 0 0 1-2 2H6a2 2 0 0 1-2-2V6a2 2 0 0 1 2-2h2" />
              <rect x="8" y="2" width="8" height="4" rx="1" ry="1" />
            </svg>
            <span className="text-[12px] font-semibold uppercase tracking-wider text-slate-400">
              Ввод параметров сделки
            </span>
          </div>
          <button
            type="button"
            id="fill-sample-btn"
            onClick={() => {
              setIsin('RU000A1038V6');
              setQuantity('100');
              resolveInstrument('RU000A1038V6');
            }}
            className="text-[11px] text-blue-400 hover:text-blue-300 font-medium transition-colors cursor-pointer"
          >
            Вставить пример (ОФЗ)
          </button>
        </div>

        <form onSubmit={handleAddSubmit} id="trade-form">
          <div className="grid grid-cols-1 sm:grid-cols-2 gap-3 mb-3">
            <div>
              <label
                htmlFor="isin-input"
                className="block text-[11px] text-slate-400 mb-1"
              >
                ISIN инструмента
              </label>
              <div className="relative">
                <input
                  id="isin-input"
                  type="text"
                  maxLength={12}
                  value={isin}
                  onChange={(e) => {
                    const val = e.target.value.toUpperCase();
                    setIsin(val);
                    if (isinError) setIsinError(null);
                  }}
                  onBlur={handleIsinBlur}
                  placeholder="RU000A1038V6"
                  className={`
                    w-full rounded-md border bg-[#080d16] px-3 py-2 font-mono text-[13px] text-white
                    placeholder:text-slate-600 transition-colors
                    focus:outline-none focus:border-blue-500
                    ${isinError ? 'border-red-500/80' : 'border-[#1a253a]'}
                  `}
                />
                {loadingInstrument && (
                  <div className="absolute right-2.5 top-1/2 -translate-y-1/2">
                    <div className="w-3.5 h-3.5 border-2 border-blue-400 border-t-transparent rounded-full animate-spin" />
                  </div>
                )}
              </div>
            </div>

            <div>
              <label
                htmlFor="quantity-input"
                className="block text-[11px] text-slate-400 mb-1"
              >
                Количество бумаг (шт.)
              </label>
              <input
                id="quantity-input"
                type="number"
                step={1}
                value={quantity}
                onChange={(e) => {
                  setQuantity(e.target.value);
                  if (quantityError) setQuantityError(null);
                }}
                placeholder="100"
                className={`
                  w-full rounded-md border bg-[#080d16] px-3 py-2 font-mono text-[13px] text-white
                  placeholder:text-slate-600 transition-colors
                  focus:outline-none focus:border-blue-500
                  ${quantityError ? 'border-red-500/80' : 'border-[#1a253a]'}
                `}
              />
              <p className="text-[10px] text-slate-500 mt-0.5">Отрицательное = шорт</p>
            </div>
          </div>

          {(isinError || quantityError) && (
            <div className="mb-3 space-y-1">
              {isinError && (
                <div id="isin-error" className="flex items-center gap-1.5 text-[11px] text-red-400">
                  <svg className="w-3.5 h-3.5 shrink-0" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
                    <circle cx="12" cy="12" r="10" />
                    <line x1="12" y1="8" x2="12" y2="12" />
                    <line x1="12" y1="16" x2="12.01" y2="16" />
                  </svg>
                  <span>{isinError}</span>
                </div>
              )}
              {quantityError && (
                <div id="quantity-error" className="flex items-center gap-1.5 text-[11px] text-red-400">
                  <svg className="w-3.5 h-3.5 shrink-0" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
                    <circle cx="12" cy="12" r="10" />
                    <line x1="12" y1="8" x2="12" y2="12" />
                    <line x1="12" y1="16" x2="12.01" y2="16" />
                  </svg>
                  <span>{quantityError}</span>
                </div>
              )}
            </div>
          )}

          <button
            id="add-trade-btn"
            type="submit"
            disabled={mutating}
            className="
              w-full rounded-md border border-[#1e2a3f] bg-[#0f1728] py-2 px-4
              text-[13px] font-medium text-slate-300
              hover:border-blue-500/60 hover:text-white hover:bg-[#142036]
              transition-all duration-150 cursor-pointer
              flex items-center justify-center gap-2
              disabled:opacity-50 disabled:cursor-not-allowed
            "
          >
            {mutating ? (
              <>
                <div className="w-3.5 h-3.5 border-2 border-slate-400 border-t-transparent rounded-full animate-spin" />
                <span>Сохранение…</span>
              </>
            ) : (
              <>
                <span className="text-base leading-none font-normal">+</span>
                <span>Добавить сделку в портфель</span>
              </>
            )}
          </button>
        </form>

        {mutError && (
          <div className="mt-3 flex items-center gap-1.5 text-[11px] text-amber-400 bg-amber-950/30 border border-amber-900/40 rounded-md px-3 py-2">
            <svg className="w-3.5 h-3.5 shrink-0" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
              <path d="m21.73 18-8-14a2 2 0 0 0-3.48 0l-8 14A2 2 0 0 0 4 21h16a2 2 0 0 0 1.73-3Z" />
              <line x1="12" y1="9" x2="12" y2="13" />
              <line x1="12" y1="17" x2="12.01" y2="17" />
            </svg>
            <span>{mutError}</span>
          </div>
        )}

        <div className="mt-5">
          <div className="border-t border-[#162033] pt-3">
            <div className="grid grid-cols-12 text-[11px] font-medium text-slate-400 pb-2 px-1">
              <span className="col-span-6">ISIN</span>
              <span className="col-span-4 text-right">Кол-во</span>
              <span className="col-span-2 text-right">Действия</span>
            </div>

            {trades.length === 0 ? (
              <div id="empty-trades" className="py-6 text-center text-xs text-slate-500">
                Нет сделок в портфеле
              </div>
            ) : (
              <div id="trade-list" className="divide-y divide-[#141b29]">
                {trades.map((trade) => {
                  const isEditing = editingTradeId === trade.id;
                  return (
                    <div
                      key={trade.id}
                      onClick={() => {
                        if (trade.instrument) onSelectInstrument(trade.instrument);
                      }}
                      className="grid grid-cols-12 items-center py-2 px-1 hover:bg-[#11192a]/50 rounded transition-colors group cursor-pointer"
                    >
                      <div className="col-span-6">
                        <span className="font-mono text-[13px] font-semibold text-slate-200">
                          {trade.isin}
                        </span>
                        {trade.instrument?.name && (
                          <p className="text-[10px] text-slate-500 truncate">{trade.instrument.name}</p>
                        )}
                      </div>

                      <div className="col-span-4 text-right">
                        {isEditing ? (
                          <div className="inline-block" onClick={(e) => e.stopPropagation()}>
                            <input
                              type="number"
                              step={1}
                              value={editQuantityValue}
                              onChange={(e) => setEditQuantityValue(e.target.value)}
                              onKeyDown={(e) => {
                                if (e.key === 'Enter') saveEditing(trade.id);
                                if (e.key === 'Escape') cancelEditing();
                              }}
                              autoFocus
                              className="w-20 rounded border border-blue-500 bg-[#080d16] px-1.5 py-0.5 text-right font-mono text-[13px] text-white focus:outline-none"
                            />
                          </div>
                        ) : (
                          <span className={`font-mono text-[13px] font-semibold ${trade.quantity < 0 ? 'text-red-400' : 'text-slate-200'}`}>
                            {formatNumber(trade.quantity, 0)}
                          </span>
                        )}
                      </div>

                      <div
                        className="col-span-2 flex items-center justify-end gap-1.5"
                        onClick={(e) => e.stopPropagation()}
                      >
                        {isEditing ? (
                          <>
                            <button
                              id={`save-trade-${trade.id}`}
                              onClick={() => saveEditing(trade.id)}
                              disabled={mutating}
                              className="p-1 text-emerald-400 hover:text-emerald-300 transition-colors disabled:opacity-50"
                              title="Сохранить"
                            >
                              <svg className="w-3.5 h-3.5" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.5">
                                <polyline points="20 6 9 17 4 12" />
                              </svg>
                            </button>
                            <button
                              id={`cancel-trade-${trade.id}`}
                              onClick={cancelEditing}
                              className="p-1 text-slate-400 hover:text-slate-300 transition-colors"
                              title="Отмена"
                            >
                              <svg className="w-3.5 h-3.5" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.5">
                                <line x1="18" y1="6" x2="6" y2="18" />
                                <line x1="6" y1="6" x2="18" y2="18" />
                              </svg>
                            </button>
                          </>
                        ) : (
                          <>
                            <button
                              id={`edit-trade-${trade.id}`}
                              onClick={() => startEditing(trade)}
                              disabled={mutating}
                              className="p-1 text-slate-400 hover:text-blue-400 transition-colors disabled:opacity-50"
                              title="Редактировать"
                            >
                              <svg className="w-3.5 h-3.5" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
                                <path d="M17 3a2.85 2.83 0 1 1 4 4L7.5 20.5 2 22l1.5-5.5Z" />
                              </svg>
                            </button>
                            <button
                              id={`remove-trade-${trade.id}`}
                              onClick={() => onRemoveTrade(trade.id)}
                              disabled={mutating}
                              className="p-1 text-slate-400 hover:text-red-400 transition-colors disabled:opacity-50"
                              title="Удалить сделку"
                            >
                              <svg className="w-3.5 h-3.5" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
                                <polyline points="3 6 5 6 21 6" />
                                <path d="M19 6v14a2 2 0 0 1-2 2H7a2 2 0 0 1-2-2V6m3 0V4a2 2 0 0 1 2-2h4a2 2 0 0 1 2 2v2" />
                              </svg>
                            </button>
                          </>
                        )}
                      </div>
                    </div>
                  );
                })}
              </div>
            )}

            {editError && (
              <p className="text-[11px] text-red-400 mt-2">{editError}</p>
            )}

            <div className="mt-3 pt-2 text-[11px] text-slate-500">
              Сделок в портфеле: {trades.length}
            </div>
          </div>
        </div>
      </div>

      <div>
        <button
          id="calculate-btn"
          onClick={onCalculate}
          disabled={calculating || trades.length === 0 || mutating}
          className="
            w-full rounded-lg bg-blue-600 hover:bg-blue-500 py-3.5 px-4
            text-[14px] font-semibold text-white shadow-md shadow-blue-900/30
            transition-all duration-150 cursor-pointer
            disabled:opacity-40 disabled:cursor-not-allowed disabled:hover:bg-blue-600
            flex items-center justify-center gap-2
          "
        >
          <svg className="w-4 h-4 text-white" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
            <rect x="4" y="2" width="16" height="20" rx="2" />
            <line x1="8" y1="6" x2="16" y2="6" />
            <line x1="16" y1="14" x2="16" y2="18" />
            <path d="M16 10h.01" />
            <path d="M12 10h.01" />
            <path d="M8 10h.01" />
            <path d="M12 14h.01" />
            <path d="M8 14h.01" />
            <path d="M12 18h.01" />
            <path d="M8 18h.01" />
          </svg>
          <span>{calculating ? 'Выполняется расчёт…' : 'Запустить расчёт риск-метрики'}</span>
        </button>

        <p className="text-[11px] text-slate-400 text-center mt-2">
          Расчёт DV01 по всем сделкам гипотетического портфеля
        </p>

        {calcError && (
          <p className="text-xs text-center text-red-400 mt-2">
            {calcError}
          </p>
        )}
      </div>
    </div>
  );
}
