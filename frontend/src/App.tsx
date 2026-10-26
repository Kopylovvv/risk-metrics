import { useState, useCallback, useEffect } from 'react';
import type { Trade, Instrument, Workspace, CalculationResult } from '@/types/api';
import {
  openWorkspace,
  getWorkspace,
  addTrade,
  updateTrade,
  deleteTrade,
  runCalculation,
  getCalculation,
  getErrorMessage,
  extractApiError,
} from '@/api/client';
import Header from '@/components/Header';
import TradeSection from '@/components/TradeSection';
import InstrumentInfo from '@/components/InstrumentInfo';
import ResultsPanel from '@/components/ResultsPanel';

export default function App() {
  const [workspace, setWorkspace] = useState<Workspace | null>(null);
  const [initLoading, setInitLoading] = useState(true);
  const [initError, setInitError] = useState<string | null>(null);

  const [selectedInstrument, setSelectedInstrument] = useState<Instrument | null>(null);

  const [calcResult, setCalcResult] = useState<CalculationResult | null>(null);
  const [isStale, setIsStale] = useState(false);
  const [timestamp, setTimestamp] = useState<string | null>(null);
  const [calculating, setCalculating] = useState(false);
  const [calcError, setCalcError] = useState<string | null>(null);

  const [mutating, setMutating] = useState(false);
  const [mutError, setMutError] = useState<string | null>(null);

  const trades: Trade[] = workspace?.trades ?? [];
  const revision = workspace?.revision ?? 0;

  useEffect(() => {
    let cancelled = false;

    async function init() {
      try {
        const ws = await openWorkspace();
        if (cancelled) return;
        setWorkspace(ws);

        if (ws.lastCalculationId) {
          try {
            const calc = await getCalculation(ws.lastCalculationId);
            if (!cancelled) {
              setCalcResult(calc);
              setIsStale(ws.revision !== calc.workspaceRevision);
              setTimestamp(
                new Date(calc.createdAt).toLocaleTimeString('ru-RU', {
                  hour: '2-digit',
                  minute: '2-digit',
                  second: '2-digit',
                }),
              );
            }
          } catch {
          }
        }
      } catch (err) {
        if (!cancelled) {
          setInitError(getErrorMessage(err));
        }
      } finally {
        if (!cancelled) setInitLoading(false);
      }
    }

    init();
    return () => { cancelled = true; };
  }, []);

  const applyWorkspace = useCallback((ws: Workspace) => {
    setWorkspace(ws);
    setIsStale(true);
    setMutError(null);
  }, []);

  const handleConflict = useCallback(async (errMsg: string) => {
    setMutError(errMsg + ' Данные обновлены.');
    try {
      const ws = await getWorkspace();
      setWorkspace(ws);
    } catch {
      // ошибка уже показана пользователю
    }
  }, []);

  const handleAddTrade = useCallback(
    async (isin: string, quantity: number) => {
      setMutating(true);
      setMutError(null);
      try {
        const ws = await addTrade(isin, quantity, revision);
        applyWorkspace(ws);
        const newTrade = ws.trades[ws.trades.length - 1];
        if (newTrade?.instrument) {
          setSelectedInstrument(newTrade.instrument);
        }
      } catch (err) {
        const apiErr = extractApiError(err);
        if (apiErr?.code === 'REVISION_CONFLICT') {
          await handleConflict(apiErr.message);
        } else {
          setMutError(getErrorMessage(err));
        }
        throw err;
      } finally {
        setMutating(false);
      }
    },
    [revision, applyWorkspace, handleConflict],
  );

  const handleRemoveTrade = useCallback(
    async (tradeId: string) => {
      setMutating(true);
      setMutError(null);
      try {
        const ws = await deleteTrade(tradeId, revision);
        applyWorkspace(ws);
      } catch (err) {
        const apiErr = extractApiError(err);
        if (apiErr?.code === 'REVISION_CONFLICT') {
          await handleConflict(apiErr.message);
        } else {
          setMutError(getErrorMessage(err));
        }
        throw err;
      } finally {
        setMutating(false);
      }
    },
    [revision, applyWorkspace, handleConflict],
  );

  const handleUpdateTrade = useCallback(
    async (tradeId: string, quantity: number) => {
      setMutating(true);
      setMutError(null);
      try {
        const ws = await updateTrade(tradeId, revision, { quantity });
        applyWorkspace(ws);
      } catch (err) {
        const apiErr = extractApiError(err);
        if (apiErr?.code === 'REVISION_CONFLICT') {
          await handleConflict(apiErr.message);
        } else {
          setMutError(getErrorMessage(err));
        }
        throw err;
      } finally {
        setMutating(false);
      }
    },
    [revision, applyWorkspace, handleConflict],
  );

  async function handleCalculate() {
    if (trades.length === 0) return;

    setCalculating(true);
    setCalcError(null);
    try {
      const result = await runCalculation(revision);
      setCalcResult(result);
      setIsStale(false);

      const now = new Date();
      setTimestamp(
        now.toLocaleTimeString('ru-RU', {
          hour: '2-digit',
          minute: '2-digit',
          second: '2-digit',
        }),
      );
    } catch (err) {
      const apiErr = extractApiError(err);
      if (apiErr?.code === 'REVISION_CONFLICT') {
        await handleConflict(apiErr.message);
        setCalcError('Набор изменился. Попробуйте ещё раз.');
      } else {
        setCalcError(getErrorMessage(err));
      }
    } finally {
      setCalculating(false);
    }
  }

  if (initLoading) {
    return (
      <div className="min-h-screen flex items-center justify-center bg-[#070b14] text-slate-100">
        <div className="text-center">
          <div className="w-10 h-10 mx-auto mb-4 border-2 border-blue-500 border-t-transparent rounded-full animate-spin" />
          <p className="text-sm text-slate-400">Подключение к серверу…</p>
        </div>
      </div>
    );
  }

  if (initError) {
    return (
      <div className="min-h-screen flex items-center justify-center bg-[#070b14] text-slate-100">
        <div className="text-center max-w-md">
          <div className="w-14 h-14 mx-auto mb-4 rounded-full bg-red-500/10 border border-red-500/30 flex items-center justify-center">
            <svg className="w-7 h-7 text-red-400" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
              <circle cx="12" cy="12" r="10" />
              <line x1="12" y1="8" x2="12" y2="12" />
              <line x1="12" y1="16" x2="12.01" y2="16" />
            </svg>
          </div>
          <p className="text-base font-medium text-red-400 mb-2">Не удалось подключиться</p>
          <p className="text-sm text-slate-400 mb-4">{initError}</p>
          <button
            onClick={() => window.location.reload()}
            className="px-4 py-2 rounded-md bg-blue-600 hover:bg-blue-500 text-sm text-white transition-colors cursor-pointer"
          >
            Попробовать снова
          </button>
        </div>
      </div>
    );
  }

  return (
    <div className="min-h-screen flex flex-col bg-[#070b14] text-slate-100 antialiased">
      <Header />

      <main className="flex-1 mx-auto w-full max-w-[1440px] px-6 py-6">
        <div className="grid grid-cols-1 lg:grid-cols-12 gap-6 items-start">
          <div className="lg:col-span-5">
            <TradeSection
              trades={trades}
              selectedInstrument={selectedInstrument}
              onSelectInstrument={setSelectedInstrument}
              onAddTrade={handleAddTrade}
              onRemoveTrade={handleRemoveTrade}
              onUpdateTrade={handleUpdateTrade}
              onCalculate={handleCalculate}
              calculating={calculating}
              calcError={calcError}
              mutating={mutating}
              mutError={mutError}
            />
          </div>

          <div className="lg:col-span-7 space-y-6">
            <InstrumentInfo instrument={selectedInstrument} />

            <ResultsPanel
              result={calcResult}
              loading={calculating}
              isStale={isStale}
              timestamp={timestamp}
            />
          </div>
        </div>
      </main>
    </div>
  );
}
