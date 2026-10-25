import { useSyncExternalStore } from 'react';
const subscribe = (cb: () => void) => { window.addEventListener('hashchange',cb); return () => window.removeEventListener('hashchange',cb); };
const snapshot = () => window.location.hash;
function useHash() { return useSyncExternalStore(subscribe,snapshot,()=> '#/VANGUARD'); }
export function usePathname() { return useHash().split('?')[0]; }
export function useSearchParams() { return new URLSearchParams(useHash().split('?')[1] || ''); }
const router = { replace: (url: string, _options?: unknown) => { window.history.replaceState(null,'',url); window.dispatchEvent(new HashChangeEvent('hashchange')); } };
export function useRouter() { return router; }
