import { useState } from 'react';
import type { Trade, Instrument } from '@/types/api';
import { fetchInstrument } from '@/api/client';
import { generateId, isValidIsin } from '@/utils/helpers';

interface TradeFormProps {
  onAdd: (trade: Trade, instrument: Instrument) => void;
  onInstrumentLoaded: (instrument: Instrument | null) => void;
}

export default function TradeForm({ onAdd, onInstrumentLoaded }: TradeFormProps) {
  const [isin, setIsin] = useState('');
  const [positionSize, setPositionSize] = useState('');
  const [instrument, setInstrument] = useState<Instrument | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  /** Поиск инструмента при потере фокуса поля ISIN */
  async function handleIsinBlur() {
    const trimmed = isin.trim().toUpperCase();
    if (!trimmed) {
      setInstrument(null);
      setError(null);
      onInstrumentLoaded(null);
      return;
    }
    if (!isValidIsin(trimmed)) {
      setInstrument(null);
      setError('Неверный формат ISIN (2 буквы + 9 алфавитно-цифровых + 1 цифра)');
      onInstrumentLoaded(null);
      return;
    }

    setLoading(true);
    setError(null);
    try {
      const result = await fetchInstrument(trimmed);
      if (result) {
        setInstrument(result);
        onInstrumentLoaded(result);
      } else {
        setInstrument(null);
        setError(`Инструмент «${trimmed}» не найден в базе`);
        onInstrumentLoaded(null);
      }
    } catch {
      setError('Ошибка соединения при поиске инструмента');
      onInstrumentLoaded(null);
    } finally {
      setLoading(false);
    }
  }

  /** Добавление сделки */
  function handleSubmit(e: React.FormEvent) {
    e.preventDefault();
    if (!instrument) return;

    const size = Number(positionSize);
    if (!size || size <= 0) {
      setError('Размер позиции должен быть положительным числом');
      return;
    }

    onAdd(
      { id: generateId(), isin: instrument.isin, positionSize: size },
      instrument,
    );

    // Сброс формы
    setIsin('');
    setPositionSize('');
    setInstrument(null);
    setError(null);
    onInstrumentLoaded(null);
  }

  const isFormValid = instrument !== null && Number(positionSize) > 0;

  return (
    <form onSubmit={handleSubmit} id="trade-form">
      {/* Inputs row */}
      <div className="flex gap-3 items-end">
        {/* ISIN */}
        <div className="flex-1 min-w-0">
          <label htmlFor="isin-input" className="block text-[11px] uppercase tracking-wider text-[var(--color-text-label)] mb-1.5">
            ISIN инструмента
          </label>
          <div className="relative">
            <input
              id="isin-input"
              type="text"
              placeholder="RU000A0ZYYN2"
              maxLength={12}
              value={isin}
              onChange={(e) => {
                setIsin(e.target.value.toUpperCase());
                if (instrument) {
                  setInstrument(null);
                  onInstrumentLoaded(null);
                }
                if (error) setError(null);
              }}
              onBlur={handleIsinBlur}
              className={`
                w-full rounded-md border bg-[var(--color-bg)] px-3 py-2 text-sm font-mono
                placeholder:text-[var(--color-text-muted)]
                transition-colors duration-150
                focus:outline-none focus:border-[var(--color-border-focus)]
                ${error ? 'border-[var(--color-danger)]/60' : 'border-[var(--color-border)]'}
              `}
            />
            {loading && (
              <div className="absolute right-2.5 top-1/2 -translate-y-1/2">
                <div className="w-4 h-4 border-2 border-blue-400 border-t-transparent rounded-full animate-spin" />
              </div>
            )}
          </div>
        </div>

        {/* Размер позиции */}
        <div className="w-[140px] shrink-0">
          <label htmlFor="position-size-input" className="block text-[11px] uppercase tracking-wider text-[var(--color-text-label)] mb-1.5">
            Размер позиции (шт.)
          </label>
          <input
            id="position-size-input"
            type="number"
            min={1}
            step={1}
            placeholder="0"
            value={positionSize}
            onChange={(e) => {
              setPositionSize(e.target.value);
              if (error) setError(null);
            }}
            className="
              w-full rounded-md border border-[var(--color-border)] bg-[var(--color-bg)] px-3 py-2 text-sm
              placeholder:text-[var(--color-text-muted)]
              transition-colors duration-150
              focus:outline-none focus:border-[var(--color-border-focus)]
            "
          />
        </div>
      </div>

      {/* Error */}
      {error && (
        <div className="mt-2 flex items-center gap-1.5 text-[12px] text-[var(--color-danger)]">
          <svg className="w-3.5 h-3.5 shrink-0" fill="none" viewBox="0 0 24 24" strokeWidth={2} stroke="currentColor">
            <path strokeLinecap="round" strokeLinejoin="round" d="M12 9v3.75m9-.75a9 9 0 1 1-18 0 9 9 0 0 1 18 0Zm-9 3.75h.008v.008H12v-.008Z" />
          </svg>
          {error}
        </div>
      )}

      {/* Add button */}
      <button
        id="add-trade-btn"
        type="submit"
        disabled={!isFormValid}
        className="
          mt-4 w-full rounded-md border border-dashed border-[var(--color-border-light)] py-2.5
          text-[13px] text-[var(--color-text-secondary)]
          hover:border-[var(--color-primary)] hover:text-[var(--color-primary)]
          transition-colors duration-150
          disabled:opacity-30 disabled:cursor-not-allowed disabled:hover:border-[var(--color-border-light)] disabled:hover:text-[var(--color-text-secondary)]
          cursor-pointer flex items-center justify-center gap-1.5
        "
      >
        <svg className="w-4 h-4" fill="none" viewBox="0 0 24 24" strokeWidth={1.5} stroke="currentColor">
          <path strokeLinecap="round" strokeLinejoin="round" d="M12 4.5v15m7.5-7.5h-15" />
        </svg>
        Добавить сделку в портфель
      </button>
    </form>
  );
}
