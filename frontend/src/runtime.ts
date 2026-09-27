// The parallel deployment serves the existing engine API through the Go
// gateway. Keep browser requests on this origin in both Docker and Vite dev;
// vite.config.ts forwards the paths during local development.
export const API_URL = import.meta.env.VITE_API_URL || window.location.origin;

export const WS_URL = import.meta.env.VITE_WS_URL
  || `${window.location.protocol === "https:" ? "wss:" : "ws:"}//${window.location.host}/ws/stream`;

export const API_TOKEN = import.meta.env.VITE_API_TOKEN || "";
