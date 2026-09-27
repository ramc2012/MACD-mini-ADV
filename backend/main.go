package main

import (
	"bytes"
	"context"
	"crypto/subtle"
	"encoding/json"
	"fmt"
	"io"
	"log"
	"net/http"
	"net/http/httputil"
	"net/url"
	"os"
	"strconv"
	"strings"
	"time"

	"github.com/nats-io/nats.go"
)

// Matches the analytics service's request body limit, so a chart the gateway
// accepts is never refused downstream.
const maxChartBytes = 32 << 20

type gateway struct {
	engine         *url.URL
	analytics      *url.URL
	engineProxy    *httputil.ReverseProxy
	analyticsProxy *httputil.ReverseProxy
	client         *http.Client
	healthClient   *http.Client
	// stream is nil when STREAM_FANOUT=off: /ws/stream is then tunnelled to
	// the engine unchanged, as before.
	stream *streamHub
	// token, when set, guards the gateway's own data endpoints the way the
	// engine guards its API. Proxied engine routes are checked by the engine.
	token string
}

func (g *gateway) authorized(r *http.Request) bool {
	if g.token == "" {
		return true
	}
	supplied := r.Header.Get("X-Macd-Token")
	if supplied == "" {
		supplied = r.URL.Query().Get("token")
	}
	return subtle.ConstantTimeCompare([]byte(supplied), []byte(g.token)) == 1
}

type dependencyHealth struct {
	Reachable  bool `json:"reachable"`
	HTTPStatus int  `json:"httpStatus,omitempty"`
}

type healthResponse struct {
	Status    string           `json:"status"`
	Gateway   string           `json:"gateway"`
	Engine    dependencyHealth `json:"engine"`
	Analytics dependencyHealth `json:"analytics"`
	Stream    *streamHealth    `json:"stream,omitempty"`
	CheckedAt string           `json:"checkedAt"`
}

type streamHealth struct {
	Mode      string `json:"mode"`
	Connected bool   `json:"connected"`
}

func main() {
	engineURL := env("ENGINE_URL", "http://engine:8100")
	analyticsURL := env("ANALYTICS_URL", "http://analytics:8081")
	port := env("PORT", "8100")

	g, err := newGateway(engineURL, analyticsURL)
	if err != nil {
		log.Fatal(err)
	}
	g.token = env("MACD_API_TOKEN", "")
	if !strings.EqualFold(env("STREAM_FANOUT", "on"), "off") {
		var bus publisher
		if natsURL := env("NATS_URL", ""); natsURL != "" {
			// Connect in the background and buffer while NATS is away: the
			// bus feeds analytics only and must never hold up the terminal.
			conn, err := nats.Connect(natsURL,
				nats.Name("parallel-gateway"),
				nats.RetryOnFailedConnect(true),
				nats.MaxReconnects(-1),
				nats.ReconnectWait(time.Second),
				nats.ReconnectBufSize(16<<20),
			)
			if err != nil {
				log.Printf("tick bus disabled: %v", err)
			} else {
				bus = conn
			}
		}
		origins := strings.Split(env("MACD_ALLOWED_ORIGINS_CSV", ""), ",")
		g.stream = newStreamHub(g.engine, g.token, origins, bus)
		go g.stream.run(context.Background())
		log.Printf("stream fan-out enabled (tick bus: %v)", bus != nil)
	}

	server := &http.Server{
		Addr:              ":" + port,
		Handler:           g,
		ReadHeaderTimeout: 10 * time.Second,
		IdleTimeout:       120 * time.Second,
		// WebSocket streams must not have a server-wide write deadline.
	}
	log.Printf("Go gateway listening on %s", server.Addr)
	log.Fatal(server.ListenAndServe())
}

func env(key, fallback string) string {
	if value := strings.TrimSpace(os.Getenv(key)); value != "" {
		return value
	}
	return fallback
}

func parseUpstream(raw string) (*url.URL, error) {
	u, err := url.Parse(raw)
	if err != nil || u.Host == "" || (u.Scheme != "http" && u.Scheme != "https") || u.RawQuery != "" || u.Fragment != "" {
		return nil, fmt.Errorf("invalid upstream URL %q: expected an http(s) origin", raw)
	}
	if u.Path != "" && u.Path != "/" {
		return nil, fmt.Errorf("invalid upstream URL %q: path prefixes are unsupported", raw)
	}
	return u, nil
}

func newGateway(engineRaw, analyticsRaw string) (*gateway, error) {
	engine, err := parseUpstream(engineRaw)
	if err != nil {
		return nil, fmt.Errorf("ENGINE_URL: %w", err)
	}
	analytics, err := parseUpstream(analyticsRaw)
	if err != nil {
		return nil, fmt.Errorf("ANALYTICS_URL: %w", err)
	}

	proxy := httputil.NewSingleHostReverseProxy(engine)
	proxy.FlushInterval = -1
	proxy.ErrorHandler = func(w http.ResponseWriter, _ *http.Request, err error) {
		log.Printf("engine proxy: %v", err)
		writeError(w, http.StatusBadGateway, "engine_unavailable", "The trading engine is unavailable")
	}
	analyticsProxy := httputil.NewSingleHostReverseProxy(analytics)
	analyticsProxy.ErrorHandler = func(w http.ResponseWriter, _ *http.Request, err error) {
		log.Printf("analytics proxy: %v", err)
		writeError(w, http.StatusBadGateway, "analytics_unavailable", "The analytics service is unavailable")
	}
	return &gateway{
		engine:         engine,
		analytics:      analytics,
		engineProxy:    proxy,
		analyticsProxy: analyticsProxy,
		client: &http.Client{
			Timeout: 90 * time.Second,
		},
		healthClient: &http.Client{
			Timeout: 3 * time.Second,
		},
	}, nil
}

func (g *gateway) ServeHTTP(w http.ResponseWriter, r *http.Request) {
	switch r.URL.Path {
	case "/parallel/health":
		if r.Method != http.MethodGet {
			w.Header().Set("Allow", http.MethodGet)
			writeError(w, http.StatusMethodNotAllowed, "method_not_allowed", "GET is required")
			return
		}
		g.health(w, r)
	case "/parallel/analytics":
		if r.Method != http.MethodGet {
			w.Header().Set("Allow", http.MethodGet)
			writeError(w, http.StatusMethodNotAllowed, "method_not_allowed", "GET is required")
			return
		}
		g.analyze(w, r)
	case "/parallel/live", "/parallel/live/scan", "/parallel/live/stats":
		if !g.authorized(r) {
			writeError(w, http.StatusUnauthorized, "invalid_token", "Invalid API token")
			return
		}
		if r.Method != http.MethodGet {
			w.Header().Set("Allow", http.MethodGet)
			writeError(w, http.StatusMethodNotAllowed, "method_not_allowed", "GET is required")
			return
		}
		// Rust owns the live analytics; it serves them as /live*.
		r.URL.Path = strings.TrimPrefix(r.URL.Path, "/parallel")
		r.URL.RawPath = ""
		g.analyticsProxy.ServeHTTP(w, r)
	case "/parallel/stream/stats":
		if !g.authorized(r) {
			writeError(w, http.StatusUnauthorized, "invalid_token", "Invalid API token")
			return
		}
		w.Header().Set("Content-Type", "application/json")
		w.Header().Set("Cache-Control", "no-store")
		if g.stream == nil {
			_ = json.NewEncoder(w).Encode(map[string]string{"mode": "tunnel"})
			return
		}
		_ = json.NewEncoder(w).Encode(g.stream.stats())
	case "/ws/stream":
		if g.stream != nil {
			g.stream.serveClient(w, r)
			return
		}
		g.engineProxy.ServeHTTP(w, r)
	default:
		if strings.HasPrefix(r.URL.Path, "/parallel/") {
			writeError(w, http.StatusNotFound, "not_found", "Unknown parallel endpoint")
			return
		}
		// The original Python engine remains the source of truth for every API,
		// authentication, order and stream endpoint.
		g.engineProxy.ServeHTTP(w, r)
	}
}

func (g *gateway) health(w http.ResponseWriter, r *http.Request) {
	type result struct {
		name   string
		health dependencyHealth
	}
	results := make(chan result, 2)
	go func() { results <- result{"engine", g.check(r.Context(), g.engine)} }()
	go func() { results <- result{"analytics", g.check(r.Context(), g.analytics)} }()

	response := healthResponse{Status: "ok", Gateway: "ok", CheckedAt: time.Now().UTC().Format(time.RFC3339)}
	for range 2 {
		item := <-results
		switch item.name {
		case "engine":
			response.Engine = item.health
		case "analytics":
			response.Analytics = item.health
		}
	}
	if g.stream != nil {
		response.Stream = &streamHealth{Mode: "fanout", Connected: g.stream.connected()}
	}
	if !response.Engine.Reachable || !response.Analytics.Reachable || (response.Stream != nil && !response.Stream.Connected) {
		response.Status = "degraded"
	}
	w.Header().Set("Content-Type", "application/json")
	w.Header().Set("Cache-Control", "no-store")
	_ = json.NewEncoder(w).Encode(response)
}

func (g *gateway) check(ctx context.Context, upstream *url.URL) dependencyHealth {
	u := *upstream
	u.Path = "/health"
	req, err := http.NewRequestWithContext(ctx, http.MethodGet, u.String(), nil)
	if err != nil {
		return dependencyHealth{}
	}
	resp, err := g.healthClient.Do(req)
	if err != nil {
		return dependencyHealth{}
	}
	defer resp.Body.Close()
	_, _ = io.Copy(io.Discard, io.LimitReader(resp.Body, 4096))
	return dependencyHealth{Reachable: resp.StatusCode < 500, HTTPStatus: resp.StatusCode}
}

func (g *gateway) analyze(w http.ResponseWriter, r *http.Request) {
	symbol := strings.TrimSpace(r.URL.Query().Get("symbol"))
	if symbol == "" || len(symbol) > 256 || strings.ContainsAny(symbol, "\r\n\x00") {
		writeError(w, http.StatusBadRequest, "invalid_symbol", "Provide a valid symbol query parameter")
		return
	}
	timeframe := r.URL.Query().Get("timeframe_seconds")
	if timeframe != "" {
		seconds, err := strconv.Atoi(timeframe)
		if err != nil || seconds < 60 || seconds > 86400 {
			writeError(w, http.StatusBadRequest, "invalid_timeframe", "timeframe_seconds must be between 60 and 86400")
			return
		}
	}

	chartURL := *g.engine
	chartURL.Path = "/api/chart/" + symbol
	chartURL.RawPath = "/api/chart/" + url.PathEscape(symbol)
	if timeframe != "" {
		chartURL.RawQuery = url.Values{"timeframe_seconds": {timeframe}}.Encode()
	}
	chartReq, err := http.NewRequestWithContext(r.Context(), http.MethodGet, chartURL.String(), nil)
	if err != nil {
		writeError(w, http.StatusInternalServerError, "request_error", "Could not form chart request")
		return
	}
	// The Python service owns the token check. Forward the caller's token.
	chartReq.Header.Set("X-Macd-Token", r.Header.Get("X-Macd-Token"))
	chartResp, err := g.client.Do(chartReq)
	if err != nil {
		writeError(w, http.StatusBadGateway, "engine_unavailable", "The trading engine is unavailable")
		return
	}
	defer chartResp.Body.Close()
	chart, err := io.ReadAll(io.LimitReader(chartResp.Body, maxChartBytes+1))
	if err != nil {
		writeError(w, http.StatusBadGateway, "chart_read_failed", "Could not read chart data")
		return
	}
	if len(chart) > maxChartBytes {
		writeError(w, http.StatusRequestEntityTooLarge, "chart_too_large", "Chart exceeds analytics size limit")
		return
	}
	if chartResp.StatusCode < 200 || chartResp.StatusCode >= 300 {
		relay(w, chartResp.StatusCode, chartResp.Header.Get("Content-Type"), chart)
		return
	}
	if !json.Valid(chart) {
		writeError(w, http.StatusBadGateway, "invalid_chart", "Trading engine returned invalid chart JSON")
		return
	}

	analyzeURL := *g.analytics
	analyzeURL.Path = "/analyze"
	analyzeReq, err := http.NewRequestWithContext(r.Context(), http.MethodPost, analyzeURL.String(), bytes.NewReader(chart))
	if err != nil {
		writeError(w, http.StatusInternalServerError, "request_error", "Could not form analytics request")
		return
	}
	analyzeReq.Header.Set("Content-Type", "application/json")
	analyzeResp, err := g.client.Do(analyzeReq)
	if err != nil {
		writeError(w, http.StatusBadGateway, "analytics_unavailable", "The analytics service is unavailable")
		return
	}
	defer analyzeResp.Body.Close()
	w.Header().Set("Content-Type", contentType(analyzeResp.Header.Get("Content-Type")))
	w.Header().Set("Cache-Control", "no-store")
	w.WriteHeader(analyzeResp.StatusCode)
	_, _ = io.Copy(w, analyzeResp.Body)
}

func relay(w http.ResponseWriter, status int, mime string, body []byte) {
	w.Header().Set("Content-Type", contentType(mime))
	w.Header().Set("Cache-Control", "no-store")
	w.WriteHeader(status)
	_, _ = w.Write(body)
}

func contentType(mime string) string {
	if mime == "" {
		return "application/json"
	}
	return mime
}

func writeError(w http.ResponseWriter, status int, code, message string) {
	w.Header().Set("Content-Type", "application/json")
	w.Header().Set("Cache-Control", "no-store")
	w.WriteHeader(status)
	_ = json.NewEncoder(w).Encode(map[string]string{"error": code, "message": message})
}
