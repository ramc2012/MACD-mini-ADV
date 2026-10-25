import { API_URL, API_TOKEN } from '../../runtime';
export function dataScope() { return new URLSearchParams(window.location.hash.split('?')[1] || '').get('scope') === 'retained' ? 'retained' : 'shadow'; }
export const api = {
  async get<T = any>(path: string, options?: {params?: Record<string,unknown>; signal?: AbortSignal; timeout?: number}): Promise<{data:T}> {
    let mapped = path.replace(/^\/api\/(vanguard|cbe)(\/|$)/,'/api/nomad/$1$2');
    if (path.startsWith('/api/mp/unified/') || path.startsWith('/api/charts/')) mapped = path.replace('/api/','/api/nomad/');
    const query = new URLSearchParams({scope:dataScope()});
    Object.entries(options?.params || {}).forEach(([k,v]) => { if(v != null) query.set(k,String(v)); });
    const response = await fetch(`${API_URL}${mapped}${mapped.includes('?')?'&':'?'}${query}`, {signal:options?.timeout?AbortSignal.any([...(options.signal?[options.signal]:[]),AbortSignal.timeout(options.timeout)]):options?.signal,headers:API_TOKEN?{'X-Macd-Token':API_TOKEN}:{}});
    if (!response.ok) { if(mapped.startsWith('/api/nomad/'))window.dispatchEvent(new CustomEvent('nomad-data-error',{detail:path})); throw new Error('Data view unavailable'); }
    return {data:await response.json() as T};
  },
};

export const getOptionOHLC = (params: {
  underlying: string;
  expiry: string;
  strike: number;
  optionType: string;
  interval?: "5minute" | "15minute" | "30minute";
  limit?: number;
  instrumentKey?: string | null;
}) =>
  api.get("/api/charts/option-ohlc", {
    params: {
      underlying: params.underlying,
      expiry: params.expiry,
      strike: params.strike,
      option_type: params.optionType,
      interval: params.interval ?? "30minute",
      limit: params.limit ?? 200,
      ...(params.instrumentKey ? { instrument_key: params.instrumentKey } : {}),
    },
    timeout: 30_000,
  });
export const getVanguardSummary = () => api.get("/api/vanguard/summary");
export const getVanguardModel = () => api.get("/api/vanguard/model");
export const getVanguardObservationFollowup = (lane: "swing" | "next_session" = "swing") =>
  api.get("/api/vanguard/observation-followup", { params: { lane } });
export const getVanguardWatchlist = (sessions = 20, source_session?: string) =>
  api.get("/api/vanguard/watchlist", { params: { sessions, source_session } });
export const getVanguardStrategyJournals = (source_session?: string, limit = 200) =>
  api.get("/api/vanguard/strategy-journals", { params: { source_session, limit } });
export const getVanguardFunnel = (ts?: string) =>
  api.get("/api/vanguard/funnel", { params: { ts } });
export const getVanguardSelection = (ts?: string, limit = 100) =>
  api.get("/api/vanguard/selection", { params: { ts, limit } });
export const getVanguardBook = (limit = 200) =>
  api.get("/api/vanguard/book", { params: { limit } });
export const getVanguardAttribution = (limit = 30) =>
  api.get("/api/vanguard/attribution", { params: { limit } });
export const getVanguardBacktests = (limit = 30) =>
  api.get("/api/vanguard/backtests", { params: { limit } });
export const getVanguardPipeline = () => api.get("/api/vanguard/pipeline");
export const getVanguardMarket = (ts?: string, limit = 400) =>
  api.get("/api/vanguard/market", { params: { ts, limit } });
export const getVanguardSymbol = (symbol: string, bars = 60, sessions = 60) =>
  api.get(`/api/vanguard/symbol/${encodeURIComponent(symbol)}`, { params: { bars, sessions } });
export const getVanguardRisk = () => api.get("/api/vanguard/risk");
export const getVanguardMp = (dt?: string) =>
  api.get("/api/vanguard/mp", { params: dt ? { dt } : undefined });
export const getMpVerdicts = () => api.get("/api/mp/unified/verdicts");
export const getVanguardCrossSection = (horizon?: number) =>
  api.get("/api/vanguard/cross-section", { params: { horizon } });
export const getVanguardSentiment = (limit = 60) =>
  api.get("/api/vanguard/sentiment", { params: { limit } });
export const getVanguardOiFutures = () => api.get("/api/vanguard/oi-futures");
export const getVanguardOiFuturesSymbol = (symbol: string, sessions = 120) =>
  api.get(`/api/vanguard/oi-futures/${encodeURIComponent(symbol)}`, { params: { sessions } });
export const getVanguardLegacyBook = (limit = 200) => api.get("/api/vanguard/legacy-book", {params:{limit}});
