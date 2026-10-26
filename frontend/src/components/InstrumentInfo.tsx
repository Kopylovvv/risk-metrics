import type { Instrument } from '@/types/api';

interface InstrumentInfoProps {
  instrument: Instrument | null;
}

export default function InstrumentInfo({ instrument }: InstrumentInfoProps) {
  if (!instrument) {
    return (
      <div className="rounded-xl border border-[#1a2438] bg-[#0c121e] p-5 shadow-sm">
        <div className="flex items-center gap-2 mb-4 pb-3 border-b border-[#162033]">
          <svg className="w-4 h-4 text-slate-400" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
            <ellipse cx="12" cy="5" rx="9" ry="3" />
            <path d="M21 12c0 1.66-4 3-9 3s-9-1.34-9-3" />
            <path d="M3 5v14c0 1.66 4 3 9 3s9-1.34 9-3V5" />
          </svg>
          <span className="text-[12px] font-semibold uppercase tracking-wider text-slate-400">
            Справочные данные по инструменту
          </span>
        </div>
        <div className="py-8 text-center text-sm text-slate-500">
          Введите ISIN в форме слева или выберите сделку для просмотра параметров
        </div>
      </div>
    );
  }

  const isFloating = instrument.couponType === 'floating';
  const couponTypeText =
    instrument.couponType === 'floating'
      ? 'Floating (Плавающий)'
      : instrument.couponType === 'fixed'
        ? 'Fixed (Фиксированный)'
        : instrument.couponType === 'zero'
          ? 'Zero (Бескупонный)'
          : '—';

  return (
    <div className="rounded-xl border border-[#1a2438] bg-[#0c121e] p-5 shadow-sm">
      <div className="flex items-center justify-between mb-5 pb-3 border-b border-[#162033]">
        <div className="flex items-center gap-2">
          <svg className="w-4 h-4 text-slate-400" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
            <ellipse cx="12" cy="5" rx="9" ry="3" />
            <path d="M21 12c0 1.66-4 3-9 3s-9-1.34-9-3" />
            <path d="M3 5v14c0 1.66 4 3 9 3s9-1.34 9-3V5" />
          </svg>
          <span className="text-[12px] font-semibold uppercase tracking-wider text-slate-400">
            Справочные данные по инструменту
          </span>
        </div>
        <span className="font-mono text-[13px] font-medium text-blue-400 tracking-wider">
          {instrument.isin}
        </span>
      </div>

      <div className="grid grid-cols-1 sm:grid-cols-3 gap-y-4 gap-x-6">
        <div>
          <span className="block text-[11px] text-slate-400 mb-1">Наименование бумаги</span>
          <span className="font-mono text-[14px] font-medium text-white tracking-wide">
            {instrument.name ?? '—'}
          </span>
        </div>

        <div>
          <span className="block text-[11px] text-slate-400 mb-1">Валюта</span>
          <span className="font-mono text-[14px] font-medium text-white tracking-wide">
            {instrument.currency}
          </span>
        </div>

        <div>
          <span className="block text-[11px] text-slate-400 mb-1">Тип купона</span>
          <span className="font-mono text-[14px] font-medium text-white tracking-wide">
            {couponTypeText}
          </span>
        </div>

        <div>
          <span className="block text-[11px] text-slate-400 mb-1">Номинал</span>
          <span className="font-mono text-[14px] font-medium text-white tracking-wide">
            {instrument.nominal ? `${Number(instrument.nominal).toLocaleString('ru-RU')} ${instrument.currency}` : '—'}
          </span>
        </div>

        <div>
          <span className="block text-[11px] text-slate-400 mb-1">Чистая цена (% от номинала)</span>
          <span className="font-mono text-[14px] font-medium text-white tracking-wide">
            {instrument.pricePct ? `${instrument.pricePct}%` : '—'}
          </span>
        </div>

        <div>
          <span className="block text-[11px] text-slate-400 mb-1">НКД (на 1 бумагу)</span>
          <span className="font-mono text-[14px] font-medium text-white tracking-wide">
            {instrument.accruedInterest
              ? `${Number(instrument.accruedInterest).toLocaleString('ru-RU', { minimumFractionDigits: 2, maximumFractionDigits: 2 })} ${instrument.currency}`
              : '—'}
          </span>
        </div>

        <div>
          <span className="block text-[11px] text-slate-400 mb-1">Купонов в год</span>
          <span className="font-mono text-[14px] font-medium text-white tracking-wide">
            {instrument.couponFrequency ?? '—'}
          </span>
        </div>

        <div>
          <span className="block text-[11px] text-slate-400 mb-1">Источник данных</span>
          <span className="font-mono text-[14px] font-medium text-white tracking-wide uppercase">
            {instrument.provider ?? '—'}
          </span>
        </div>

        <div>
          <span className="block text-[11px] text-slate-400 mb-1">Дата данных</span>
          <span className="font-mono text-[14px] font-medium text-white tracking-wide">
            {instrument.dataAsOf ?? '—'}
          </span>
        </div>

        <div>
          <span className="block text-[11px] text-slate-400 mb-1">Тип инструмента</span>
          <span className="font-mono text-[14px] font-medium text-white tracking-wide">
            {instrument.instrumentType}
          </span>
        </div>
      </div>

      {isFloating && (
        <div className="mt-5 flex items-center gap-2 rounded-lg border border-blue-900/60 bg-blue-950/30 px-3.5 py-2.5 text-[12px] text-blue-300">
          <svg className="w-4 h-4 shrink-0 text-blue-400" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
            <circle cx="12" cy="12" r="10" />
            <line x1="12" y1="16" x2="12" y2="12" />
            <line x1="12" y1="8" x2="12.01" y2="8" />
          </svg>
          <span>
            Тип купона Floating: Duration устанавливается как 1/количество выплат, Convexity = 0.
          </span>
        </div>
      )}
    </div>
  );
}
