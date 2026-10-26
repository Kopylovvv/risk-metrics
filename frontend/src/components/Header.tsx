export default function Header() {
  return (
    <header className="bg-[#0b0f1a] border-b border-[#182235]">
      <div className="mx-auto max-w-[1440px] px-6 py-3.5 flex items-center justify-between">
        <div className="flex items-center gap-3">
          <div className="w-9 h-9 rounded-lg bg-[#111a2e] border border-[#1e2d4a] flex items-center justify-center shadow-inner">
            <svg
              className="w-5 h-5 text-blue-400"
              viewBox="0 0 24 24"
              fill="none"
              stroke="currentColor"
              strokeWidth="2.2"
              strokeLinecap="round"
              strokeLinejoin="round"
            >
              <polyline points="22 12 18 12 15 21 9 3 6 12 2 12" />
            </svg>
          </div>
          <div>
            <h1 className="text-[15px] font-semibold tracking-tight text-white leading-tight">
              Система расчёта рыночного риска
            </h1>
            <p className="text-[12px] text-slate-400 leading-tight">
              Модуль оценки гипотетических сделок (MVP)
            </p>
          </div>
        </div>

        <div className="flex items-center gap-3">
          {import.meta.env.VITE_USE_MOCKS === 'true' && (
            <span className="text-[11px] text-amber-400/80 bg-amber-400/10 border border-amber-400/20 rounded px-2 py-0.5">
              mock
            </span>
          )}
          <div className="flex items-center gap-2 bg-[#101726] border border-[#1e2a3f] rounded-lg px-3.5 py-1.5 text-xs text-slate-300 font-medium">
            <svg
              className="w-3.5 h-3.5 text-slate-400"
              viewBox="0 0 24 24"
              fill="none"
              stroke="currentColor"
              strokeWidth="2"
              strokeLinecap="round"
              strokeLinejoin="round"
            >
              <path d="M19 21v-2a4 4 0 0 0-4-4H9a4 4 0 0 0-4 4v2" />
              <circle cx="12" cy="7" r="4" />
            </svg>
            <span>Риск-аналитик</span>
          </div>
        </div>
      </div>
    </header>
  );
}
