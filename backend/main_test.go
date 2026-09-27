package main

import (
	"bufio"
	"encoding/json"
	"fmt"
	"io"
	"net"
	"net/http"
	"net/http/httptest"
	"strings"
	"testing"
	"time"
)

func TestProxyPreservesEngineRoutesAndRequests(t *testing.T) {
	engine := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		if r.URL.Path != "/api/orders" || r.URL.RawQuery != "lane=blast" || r.Method != http.MethodPost {
			t.Errorf("unexpected proxied request: %s %s", r.Method, r.URL.String())
		}
		if r.Header.Get("X-Macd-Token") != "secret" {
			t.Error("gateway did not preserve API token")
		}
		body, _ := io.ReadAll(r.Body)
		if string(body) != `{"symbol":"NSE:OPTION","side":"BUY"}` {
			t.Errorf("unexpected body: %s", body)
		}
		w.Header().Set("Content-Type", "application/json")
		w.WriteHeader(http.StatusCreated)
		_, _ = w.Write([]byte(`{"status":"filled"}`))
	}))
	defer engine.Close()
	analytics := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {}))
	defer analytics.Close()
	g, err := newGateway(engine.URL, analytics.URL)
	if err != nil {
		t.Fatal(err)
	}

	req := httptest.NewRequest(http.MethodPost, "/api/orders?lane=blast", strings.NewReader(`{"symbol":"NSE:OPTION","side":"BUY"}`))
	req.Header.Set("X-Macd-Token", "secret")
	w := httptest.NewRecorder()
	g.ServeHTTP(w, req)
	if w.Code != http.StatusCreated || w.Body.String() != `{"status":"filled"}` {
		t.Fatalf("unexpected proxy response: %d %s", w.Code, w.Body.String())
	}
}

func TestAnalyticsUsesEngineChartAndRelaysRustResponse(t *testing.T) {
	chart := `{"symbol":"NSE:NIFTY50-INDEX","timeframe_seconds":900,"candles":[{"close":42}],"indicators":[]}`
	engine := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		if r.URL.Path != "/api/chart/NSE:NIFTY50-INDEX" || r.URL.Query().Get("timeframe_seconds") != "900" {
			t.Errorf("wrong chart request: %s", r.URL.String())
		}
		if r.Header.Get("X-Macd-Token") != "secret" {
			t.Error("chart request lost API token")
		}
		_, _ = w.Write([]byte(chart))
	}))
	defer engine.Close()
	analytics := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		if r.Method != http.MethodPost || r.URL.Path != "/analyze" {
			t.Errorf("wrong analytics request: %s %s", r.Method, r.URL.Path)
		}
		if r.Header.Get("Content-Type") != "application/json" {
			t.Error("chart was not sent as JSON")
		}
		body, _ := io.ReadAll(r.Body)
		if string(body) != chart {
			t.Errorf("chart changed in transit: %s", body)
		}
		w.Header().Set("Content-Type", "application/json")
		_, _ = w.Write([]byte(`{"symbol":"NSE:NIFTY50-INDEX","trend":"bullish"}`))
	}))
	defer analytics.Close()
	g, err := newGateway(engine.URL, analytics.URL)
	if err != nil {
		t.Fatal(err)
	}

	req := httptest.NewRequest(http.MethodGet, "/parallel/analytics?symbol=NSE%3ANIFTY50-INDEX&timeframe_seconds=900", nil)
	req.Header.Set("X-Macd-Token", "secret")
	w := httptest.NewRecorder()
	g.ServeHTTP(w, req)
	if w.Code != http.StatusOK || !strings.Contains(w.Body.String(), `"trend":"bullish"`) {
		t.Fatalf("unexpected analytics response: %d %s", w.Code, w.Body.String())
	}
}

func TestAnalyticsPreservesEngineErrors(t *testing.T) {
	engine := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		w.Header().Set("Content-Type", "application/json")
		w.WriteHeader(http.StatusUnauthorized)
		_, _ = w.Write([]byte(`{"detail":"Invalid API token"}`))
	}))
	defer engine.Close()
	analyzed := false
	analytics := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		analyzed = true
	}))
	defer analytics.Close()
	g, _ := newGateway(engine.URL, analytics.URL)
	w := httptest.NewRecorder()
	g.ServeHTTP(w, httptest.NewRequest(http.MethodGet, "/parallel/analytics?symbol=NSE%3AOPTION", nil))
	if w.Code != http.StatusUnauthorized || w.Body.String() != `{"detail":"Invalid API token"}` || analyzed {
		t.Fatalf("engine error was not preserved: %d %s analyzed=%t", w.Code, w.Body.String(), analyzed)
	}
}

func TestParallelHealthReportsDependencies(t *testing.T) {
	engine := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		if r.URL.Path != "/health" {
			t.Error("wrong engine health route")
		}
		_, _ = w.Write([]byte(`{"ok":true}`))
	}))
	defer engine.Close()
	analytics := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		w.WriteHeader(http.StatusServiceUnavailable)
	}))
	defer analytics.Close()
	g, _ := newGateway(engine.URL, analytics.URL)
	w := httptest.NewRecorder()
	g.ServeHTTP(w, httptest.NewRequest(http.MethodGet, "/parallel/health", nil))
	var health healthResponse
	if err := json.Unmarshal(w.Body.Bytes(), &health); err != nil {
		t.Fatal(err)
	}
	if w.Code != http.StatusOK || health.Status != "degraded" || !health.Engine.Reachable || health.Analytics.Reachable || health.Analytics.HTTPStatus != 503 {
		t.Fatalf("unexpected health response: %+v", health)
	}
}

func TestWebSocketUpgradeIsTunneled(t *testing.T) {
	engine := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		if r.URL.Path != "/ws/stream" || r.URL.Query().Get("token") != "secret" {
			t.Errorf("wrong websocket route: %s", r.URL.String())
		}
		hijacker, ok := w.(http.Hijacker)
		if !ok {
			t.Error("upstream does not support hijacking")
			return
		}
		conn, rw, err := hijacker.Hijack()
		if err != nil {
			t.Error(err)
			return
		}
		defer conn.Close()
		_, _ = rw.WriteString("HTTP/1.1 101 Switching Protocols\r\nConnection: Upgrade\r\nUpgrade: websocket\r\n\r\n")
		_ = rw.Flush()
		buf := make([]byte, 4)
		if _, err := io.ReadFull(rw, buf); err == nil {
			_, _ = rw.Write(buf)
			_ = rw.Flush()
		}
	}))
	defer engine.Close()
	analytics := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {}))
	defer analytics.Close()
	g, _ := newGateway(engine.URL, analytics.URL)
	server := httptest.NewServer(g)
	defer server.Close()

	conn, err := net.Dial("tcp", strings.TrimPrefix(server.URL, "http://"))
	if err != nil {
		t.Fatal(err)
	}
	defer conn.Close()
	_ = conn.SetDeadline(time.Now().Add(5 * time.Second))
	_, _ = fmt.Fprintf(conn, "GET /ws/stream?token=secret HTTP/1.1\r\nHost: gateway\r\nConnection: Upgrade\r\nUpgrade: websocket\r\nSec-WebSocket-Key: dGVzdA==\r\nSec-WebSocket-Version: 13\r\n\r\n")
	reader := bufio.NewReader(conn)
	status, err := reader.ReadString('\n')
	if err != nil || !strings.Contains(status, "101") {
		t.Fatalf("upgrade failed: %q %v", status, err)
	}
	for {
		line, err := reader.ReadString('\n')
		if err != nil {
			t.Fatal(err)
		}
		if line == "\r\n" {
			break
		}
	}
	_, _ = conn.Write([]byte("ping"))
	buf := make([]byte, 4)
	if _, err := io.ReadFull(reader, buf); err != nil || string(buf) != "ping" {
		t.Fatalf("websocket tunnel failed: %q %v", buf, err)
	}
}
