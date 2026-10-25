import type {PositionsPayload} from '@/lib/strategy-stats';
// Retained books stay frozen; current MACD paper books have their own native stream.
export function useStrategyPositionsStream(_options?: unknown) { return {data:null as Record<string,PositionsPayload>|null,isStreamConnected:false,connectionState:'retained snapshot',error:null}; }
export function selectStrategySlice(data:Record<string,PositionsPayload>|null|undefined,lane:string) { return data?.[lane]; }
