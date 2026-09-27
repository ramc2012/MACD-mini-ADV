package main

import (
	"context"
	"encoding/json"
	"errors"
	"io"
	"log"
	"net/http"
	"os"
	"os/signal"
	"strings"
	"sync"
	"syscall"
	"time"
)

type Server struct {
	ledger    *Ledger
	market    *Market
	spot      *SpotEngine
	stocks    map[string]bool
	historyMu sync.Mutex
}

func respond(w http.ResponseWriter, status int, value any) {
	w.Header().Set("Content-Type", "application/json; charset=utf-8")
	w.Header().Set("Cache-Control", "no-store")
	w.WriteHeader(status)
	_ = json.NewEncoder(w).Encode(value)
}

func fail(w http.ResponseWriter, status int, message string) {
	respond(w, status, map[string]string{"error": message})
}

func decode(r *http.Request, destination any) error {
	decoder := json.NewDecoder(io.LimitReader(r.Body, 16*1024))
	decoder.DisallowUnknownFields()
	return decoder.Decode(destination)
}

func (s *Server) routes() http.Handler {
	mux := http.NewServeMux()
	mux.HandleFunc("GET /health", func(w http.ResponseWriter, r *http.Request) { respond(w, 200, map[string]string{"status": "ok"}) })
	mux.HandleFunc("GET /api/state", s.state)
	mux.HandleFunc("GET /api/settings", s.settings)
	mux.HandleFunc("POST /api/settings", s.saveSettings)
	mux.HandleFunc("GET /api/history", s.history)
	mux.HandleFunc("GET /api/options/probe", s.probeOptions)
	mux.HandleFunc("GET /api/spot/probe", s.probeSpot)
	mux.HandleFunc("GET /api/spot/bars", s.spotBars)
	mux.HandleFunc("POST /api/focus", s.focus)
	mux.HandleFunc("POST /api/spot/automation", s.spotAutomation)
	mux.HandleFunc("POST /api/orders", func(w http.ResponseWriter, r *http.Request) {
		fail(w, 409, "manual orders are disabled; the spot paper account is managed by the automatic strategy")
	})
	return mux
}

func (s *Server) spotBars(w http.ResponseWriter, r *http.Request) {
	symbol := strings.ToUpper(strings.TrimSpace(r.URL.Query().Get("symbol")))
	if !s.stocks[symbol] {
		fail(w, 400, "choose one of the 100 stock symbols")
		return
	}
	bars, err := s.ledger.LoadSpotBars(symbol)
	if err != nil {
		fail(w, 500, "observed minute bars unavailable")
		return
	}
	respond(w, 200, map[string]any{"symbol": symbol, "source": "Finnhub WebSocket trades", "bars": bars})
}

func (s *Server) spotAutomation(w http.ResponseWriter, r *http.Request) {
	var input struct {
		Enabled bool `json:"enabled"`
	}
	if err := decode(r, &input); err != nil {
		fail(w, 400, "invalid automation JSON")
		return
	}
	if err := s.spot.SetAuto(input.Enabled); err != nil {
		fail(w, 500, "could not update automation")
		return
	}
	respond(w, 200, s.spot.Status())
}

func (s *Server) probeSpot(w http.ResponseWriter, r *http.Request) {
	now := time.Now()
	bars, err := s.market.FetchMinuteCandles(r.Context(), "AAPL", now.Add(-4*time.Hour), now)
	if err != nil {
		respond(w, 200, map[string]any{"available": false, "reason": err.Error()})
		return
	}
	last := ""
	if len(bars) > 0 {
		last = bars[len(bars)-1].At.Format(time.RFC3339)
	}
	respond(w, 200, map[string]any{"available": len(bars) > 0, "bars": len(bars), "last_bar": last})
}

func (s *Server) focus(w http.ResponseWriter, r *http.Request) {
	var input struct {
		Symbol string `json:"symbol"`
	}
	if err := decode(r, &input); err != nil {
		fail(w, 400, "invalid symbol JSON")
		return
	}
	symbol := strings.ToUpper(strings.TrimSpace(input.Symbol))
	if !s.market.SetFocus(symbol) {
		fail(w, 400, "choose one of the 100 stock symbols")
		return
	}
	respond(w, 200, map[string]string{"symbol": symbol})
}

func (s *Server) probeOptions(w http.ResponseWriter, r *http.Request) {
	result := s.market.ProbeOptions(r.Context(), r.URL.Query().Get("refresh") == "1")
	respond(w, 200, result)
}

func (s *Server) state(w http.ResponseWriter, r *http.Request) {
	quotes := s.market.Quotes()
	book, err := s.ledger.Book(quotes)
	if err != nil {
		fail(w, 500, "paper ledger unavailable")
		return
	}
	respond(w, 200, map[string]any{
		"account": book.Account, "positions": book.Positions, "fills": book.Fills,
		"equity_history": book.EquityHistory, "universe": stockUniverse(),
		"indices": indexGauges, "quotes": quotes, "feed": s.market.Status(),
		"market": marketClock(time.Now()),
		"spot":   s.spot.Status(),
		"risk":   map[string]any{"max_position_pct": maxPositionWeight * 100, "long_only": true, "cash_only": true, "fill_rule": "fresh Finnhub price during regular NYSE hours"},
	})
}

func (s *Server) settings(w http.ResponseWriter, r *http.Request) {
	status := s.market.Status()
	respond(w, 200, map[string]bool{"finnhub_configured": status.FinnhubConfigured, "alpha_vantage_configured": status.AlphaVantageConfigured})
}

func (s *Server) saveSettings(w http.ResponseWriter, r *http.Request) {
	var input struct {
		FinnhubKey        string `json:"finnhub_key"`
		AlphaVantageKey   string `json:"alpha_vantage_key"`
		ClearFinnhub      bool   `json:"clear_finnhub"`
		ClearAlphaVantage bool   `json:"clear_alpha_vantage"`
	}
	if err := decode(r, &input); err != nil {
		fail(w, 400, "invalid settings JSON")
		return
	}
	if err := s.market.SaveKeys(input.FinnhubKey, input.AlphaVantageKey, input.ClearFinnhub, input.ClearAlphaVantage); err != nil {
		fail(w, 500, "could not save provider keys")
		return
	}
	s.settings(w, r)
}

func (s *Server) history(w http.ResponseWriter, r *http.Request) {
	symbol := strings.ToUpper(strings.TrimSpace(r.URL.Query().Get("symbol")))
	if !s.stocks[symbol] {
		fail(w, 400, "choose one of the 100 stock symbols")
		return
	}
	s.historyMu.Lock()
	defer s.historyMu.Unlock()
	cached, err := s.ledger.CachedHistory(symbol)
	if err != nil {
		fail(w, 500, "history cache unavailable")
		return
	}
	if cached.FetchedAt != "" {
		fetched, parseErr := time.Parse(time.RFC3339Nano, cached.FetchedAt)
		if parseErr == nil && time.Since(fetched) < 6*time.Hour {
			respond(w, 200, cached)
			return
		}
	}
	bars, err := s.market.FetchDaily(r.Context(), symbol)
	if err != nil {
		if len(cached.Bars) > 0 {
			respond(w, 200, cached)
			return
		}
		fail(w, 502, err.Error())
		return
	}
	if err = s.ledger.SaveHistory(symbol, bars); err != nil {
		fail(w, 500, "could not cache daily history")
		return
	}
	updated, err := s.ledger.CachedHistory(symbol)
	if err != nil {
		fail(w, 500, "history cache unavailable")
		return
	}
	respond(w, 200, updated)
}

func (s *Server) order(w http.ResponseWriter, r *http.Request) {
	var input struct {
		Symbol   string `json:"symbol"`
		Side     string `json:"side"`
		Quantity int64  `json:"quantity"`
	}
	if err := decode(r, &input); err != nil {
		fail(w, 400, "invalid order JSON")
		return
	}
	symbol := strings.ToUpper(strings.TrimSpace(input.Symbol))
	side := strings.ToUpper(strings.TrimSpace(input.Side))
	if !s.stocks[symbol] {
		fail(w, 400, "paper orders are limited to the 100 stocks")
		return
	}
	if side != "BUY" && side != "SELL" {
		fail(w, 400, "side must be BUY or SELL")
		return
	}
	if input.Quantity < 1 || input.Quantity > 100000 {
		fail(w, 400, "quantity must be 1–100,000 whole shares")
		return
	}
	clock := marketClock(time.Now())
	if !clock.Open {
		fail(w, 409, "regular NYSE session is closed; no after-hours paper fills")
		return
	}
	quotes := s.market.Quotes()
	quote, ok := quotes[symbol]
	if !ok || quote.Price <= 0 {
		fail(w, 409, "no Finnhub price is available for this stock")
		return
	}
	tradeAt, err := time.Parse(time.RFC3339Nano, quote.TradeAt)
	if err != nil || time.Since(tradeAt) > 30*time.Second || time.Until(tradeAt) > 5*time.Second {
		fail(w, 409, "Finnhub price is stale; paper fill blocked")
		return
	}
	fill, err := s.ledger.Place(symbol, side, input.Quantity, quote, quotes)
	if err != nil {
		fail(w, 409, err.Error())
		return
	}
	book, err := s.ledger.Book(quotes)
	if err == nil {
		_ = s.ledger.RecordEquity(book.Account)
	}
	respond(w, 201, fill)
}

type MarketClock struct {
	Open    bool   `json:"open"`
	NowET   string `json:"now_et"`
	Session string `json:"session"`
}

func marketClock(now time.Time) MarketClock {
	location, _ := time.LoadLocation("America/New_York")
	if location == nil {
		location = time.UTC
	}
	local := now.In(location)
	minute := local.Hour()*60 + local.Minute()
	open := local.Weekday() != time.Saturday && local.Weekday() != time.Sunday && minute >= 9*60+30 && minute < 16*60
	session := "closed"
	if open {
		session = "regular"
	}
	return MarketClock{open, local.Format(time.RFC3339), session}
}

func (l *Ledger) RecordEquity(account Account) error {
	l.mu.Lock()
	defer l.mu.Unlock()
	if account.Equity <= 0 {
		return errors.New("invalid equity")
	}
	at := time.Now().UTC().Truncate(time.Minute).Format(time.RFC3339)
	_, err := l.db.Exec("INSERT OR REPLACE INTO equity_snapshots(at,equity_cents) VALUES(?,?)", at, cents(account.Equity))
	return err
}

func main() {
	path := os.Getenv("US_DATA_DIR")
	if path == "" {
		path = "/data"
	}
	ledger, err := NewLedger(path + "/paper.sqlite3")
	if err != nil {
		log.Fatal(err)
	}
	defer ledger.Close()
	market := NewMarket(path + "/keys.json")
	spot, err := NewSpotEngine(market, ledger)
	if err != nil {
		log.Fatal(err)
	}
	server := &Server{ledger: ledger, market: market, spot: spot, stocks: stockSet()}
	ctx, stop := signal.NotifyContext(context.Background(), syscall.SIGINT, syscall.SIGTERM)
	defer stop()
	go market.Run(ctx)
	go spot.Run(ctx)
	go func() {
		ticker := time.NewTicker(time.Minute)
		defer ticker.Stop()
		for {
			select {
			case <-ctx.Done():
				return
			case <-ticker.C:
				if book, err := ledger.Book(market.Quotes()); err == nil {
					_ = ledger.RecordEquity(book.Account)
				}
			}
		}
	}()
	address := os.Getenv("US_LISTEN")
	if address == "" {
		address = ":8080"
	}
	httpServer := &http.Server{Addr: address, Handler: server.routes(), ReadHeaderTimeout: 5 * time.Second}
	go func() {
		<-ctx.Done()
		shutdown, cancel := context.WithTimeout(context.Background(), 5*time.Second)
		defer cancel()
		_ = httpServer.Shutdown(shutdown)
	}()
	log.Printf("US paper server listening on %s", address)
	if err := httpServer.ListenAndServe(); err != nil && err != http.ErrServerClosed {
		log.Fatal(err)
	}
}
