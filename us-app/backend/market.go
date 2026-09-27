package main

import (
	"context"
	"encoding/json"
	"errors"
	"fmt"
	"net/http"
	"net/url"
	"os"
	"path/filepath"
	"strings"
	"sync"
	"time"

	"github.com/gorilla/websocket"
)

type Quote struct {
	Symbol        string  `json:"symbol"`
	Price         float64 `json:"price"`
	PreviousClose float64 `json:"previous_close"`
	ChangePct     float64 `json:"change_pct"`
	Volume        float64 `json:"volume"`
	TradeAt       string  `json:"trade_at"`
	ObservedAt    string  `json:"observed_at"`
	Source        string  `json:"source"`
}

type FeedStatus struct {
	Status                 string `json:"status"`
	Error                  string `json:"error,omitempty"`
	Quotes                 int    `json:"quotes"`
	LastTradeAt            string `json:"last_trade_at,omitempty"`
	FinnhubConfigured      bool   `json:"finnhub_configured"`
	AlphaVantageConfigured bool   `json:"alpha_vantage_configured"`
	RESTError              string `json:"rest_error,omitempty"`
	WebsocketSubscriptions int    `json:"websocket_subscriptions"`
}

type keyFile struct {
	Finnhub      string `json:"finnhub"`
	AlphaVantage string `json:"alpha_vantage"`
}

type Market struct {
	mu            sync.RWMutex
	quotes        map[string]Quote
	tradeQuotes   map[string]Quote
	keys          keyFile
	keyPath       string
	status        string
	lastError     string
	restError     string
	lastTradeAt   string
	conn          *websocket.Conn
	wake          chan struct{}
	client        *http.Client
	allowed       map[string]bool
	tradable      map[string]bool
	wsLimit       int
	focus         string
	optionProbe   *OptionsCapability
	optionChecked time.Time
	trades        chan TradeTick
	droppedTrades uint64
}

func NewMarket(keyPath string) *Market {
	m := &Market{
		quotes: make(map[string]Quote), tradeQuotes: make(map[string]Quote), keyPath: keyPath, status: "waiting_for_key",
		wake: make(chan struct{}, 1), client: &http.Client{Timeout: 12 * time.Second},
		allowed:  stockSet(),
		tradable: stockSet(),
		wsLimit:  32,
		trades:   make(chan TradeTick, 16384),
	}
	for _, gauge := range indexGauges {
		m.allowed[gauge.Symbol] = true
	}
	m.keys = keyFile{Finnhub: os.Getenv("US_FINNHUB_API_KEY"), AlphaVantage: os.Getenv("US_ALPHA_VANTAGE_API_KEY")}
	if bytes, err := os.ReadFile(keyPath); err == nil {
		var saved keyFile
		if json.Unmarshal(bytes, &saved) == nil {
			m.keys = saved
		}
	}
	return m
}

func (m *Market) Keys() keyFile { m.mu.RLock(); defer m.mu.RUnlock(); return m.keys }

func (m *Market) SaveKeys(finnhub, alpha string, clearFinnhub, clearAlpha bool) error {
	m.mu.Lock()
	defer m.mu.Unlock()
	next := m.keys
	if clearFinnhub {
		next.Finnhub = ""
	} else if strings.TrimSpace(finnhub) != "" {
		next.Finnhub = strings.TrimSpace(finnhub)
	}
	if clearAlpha {
		next.AlphaVantage = ""
	} else if strings.TrimSpace(alpha) != "" {
		next.AlphaVantage = strings.TrimSpace(alpha)
	}
	if err := os.MkdirAll(filepath.Dir(m.keyPath), 0700); err != nil {
		return err
	}
	bytes, err := json.Marshal(next)
	if err != nil {
		return err
	}
	temporary := m.keyPath + ".tmp"
	if err = os.WriteFile(temporary, bytes, 0600); err != nil {
		return err
	}
	if err = os.Chmod(temporary, 0600); err != nil {
		return err
	}
	if err = os.Rename(temporary, m.keyPath); err != nil {
		return err
	}
	changed := next.Finnhub != m.keys.Finnhub
	if next.AlphaVantage != m.keys.AlphaVantage {
		m.optionProbe = nil
	}
	m.keys = next
	if changed && m.conn != nil {
		_ = m.conn.Close()
	}
	select {
	case m.wake <- struct{}{}:
	default:
	}
	return nil
}

func (m *Market) Status() FeedStatus {
	m.mu.RLock()
	defer m.mu.RUnlock()
	return FeedStatus{m.status, m.lastError, len(m.quotes), m.lastTradeAt, m.keys.Finnhub != "", m.keys.AlphaVantage != "", m.restError, m.wsLimit}
}

func (m *Market) Quotes() map[string]Quote {
	m.mu.RLock()
	defer m.mu.RUnlock()
	copy := make(map[string]Quote, len(m.quotes))
	for k, v := range m.quotes {
		copy[k] = v
	}
	return copy
}

func (m *Market) Quote(symbol string) (Quote, bool) {
	m.mu.RLock()
	defer m.mu.RUnlock()
	quote, ok := m.quotes[symbol]
	return quote, ok
}

func (m *Market) TradeQuote(symbol string) (Quote, bool) {
	m.mu.RLock()
	defer m.mu.RUnlock()
	quote, ok := m.tradeQuotes[symbol]
	return quote, ok
}

func (m *Market) SetFocus(symbol string) bool {
	if !m.tradable[symbol] {
		return false
	}
	m.mu.Lock()
	m.focus = symbol
	m.mu.Unlock()
	return true
}

func (m *Market) update(quote Quote) {
	if quote.Price <= 0 || !m.allowed[quote.Symbol] {
		return
	}
	m.mu.Lock()
	defer m.mu.Unlock()
	if quote.Source == "Finnhub trade" {
		m.tradeQuotes[quote.Symbol] = quote
	}
	prior := m.quotes[quote.Symbol]
	if prior.TradeAt != "" && quote.TradeAt != "" {
		oldTime, oldErr := time.Parse(time.RFC3339Nano, prior.TradeAt)
		newTime, newErr := time.Parse(time.RFC3339Nano, quote.TradeAt)
		if oldErr == nil && newErr == nil && newTime.Before(oldTime) {
			return
		}
	}
	if quote.PreviousClose <= 0 {
		quote.PreviousClose = prior.PreviousClose
	}
	if quote.PreviousClose > 0 {
		quote.ChangePct = (quote.Price/quote.PreviousClose - 1) * 100
	}
	m.quotes[quote.Symbol] = quote
	if quote.Source == "Finnhub trade" {
		m.lastTradeAt = quote.TradeAt
	}
}

func (m *Market) setStatus(status, detail string) {
	m.mu.Lock()
	defer m.mu.Unlock()
	m.status = status
	m.lastError = detail
}

func (m *Market) Run(ctx context.Context) {
	go m.runREST(ctx)
	backoff := 2 * time.Second
	for ctx.Err() == nil {
		key := m.Keys().Finnhub
		if key == "" {
			m.setStatus("waiting_for_key", "")
			select {
			case <-ctx.Done():
				return
			case <-m.wake:
				continue
			}
		}
		m.setStatus("connecting", "")
		endpoint := "wss://ws.finnhub.io?token=" + url.QueryEscape(key)
		connection, _, err := websocket.DefaultDialer.DialContext(ctx, endpoint, nil)
		if err != nil {
			m.setStatus("error", redact(err.Error(), key))
		} else {
			m.mu.Lock()
			m.conn = connection
			m.mu.Unlock()
			err = m.consume(ctx, connection)
			m.mu.Lock()
			if m.conn == connection {
				m.conn = nil
			}
			m.mu.Unlock()
			_ = connection.Close()
			if ctx.Err() == nil {
				if strings.Contains(strings.ToLower(err.Error()), "too many symbols") {
					m.mu.Lock()
					if m.wsLimit > 4 {
						m.wsLimit /= 2
					}
					m.mu.Unlock()
					backoff = 2 * time.Second
				}
				m.setStatus("error", redact(err.Error(), key))
			}
		}
		if ctx.Err() != nil {
			return
		}
		select {
		case <-ctx.Done():
			return
		case <-m.wake:
			backoff = 2 * time.Second
		case <-time.After(backoff):
			if backoff < 30*time.Second {
				backoff *= 2
			}
		}
	}
}

func (m *Market) consume(ctx context.Context, conn *websocket.Conn) error {
	m.mu.RLock()
	limit := m.wsLimit
	m.mu.RUnlock()
	symbols := make([]string, 0, limit)
	for _, row := range stockUniverse() {
		if len(symbols) >= limit {
			break
		}
		symbols = append(symbols, row.Symbol)
	}
	for _, symbol := range symbols {
		if err := conn.WriteJSON(map[string]string{"type": "subscribe", "symbol": symbol}); err != nil {
			return err
		}
		select {
		case <-ctx.Done():
			return ctx.Err()
		case <-time.After(15 * time.Millisecond):
		}
	}
	m.setStatus("connected", "")
	for {
		_, payload, err := conn.ReadMessage()
		if err != nil {
			return err
		}
		var event struct {
			Type string `json:"type"`
			Data []struct {
				Symbol    string  `json:"s"`
				Price     float64 `json:"p"`
				Timestamp int64   `json:"t"`
				Volume    float64 `json:"v"`
			} `json:"data"`
			Message string `json:"msg"`
		}
		if json.Unmarshal(payload, &event) != nil {
			continue
		}
		if event.Type == "error" {
			return errors.New(event.Message)
		}
		if event.Type != "trade" {
			continue
		}
		for _, trade := range event.Data {
			if trade.Price <= 0 || trade.Timestamp <= 0 {
				continue
			}
			m.update(Quote{Symbol: trade.Symbol, Price: trade.Price, Volume: trade.Volume,
				TradeAt:    time.UnixMilli(trade.Timestamp).UTC().Format(time.RFC3339Nano),
				ObservedAt: time.Now().UTC().Format(time.RFC3339Nano), Source: "Finnhub trade"})
			if m.tradable[trade.Symbol] {
				select {
				case m.trades <- TradeTick{trade.Symbol, trade.Price, trade.Volume, time.UnixMilli(trade.Timestamp).UTC()}:
				default:
					m.mu.Lock()
					m.droppedTrades++
					m.mu.Unlock()
				}
			}
		}
	}
}

func (m *Market) runREST(ctx context.Context) {
	symbols := make([]string, 0, 104)
	for _, gauge := range indexGauges {
		symbols = append(symbols, gauge.Symbol)
	}
	for _, row := range stockUniverse() {
		symbols = append(symbols, row.Symbol)
	}
	i := 0
	requestNumber := 0
	for ctx.Err() == nil {
		key := m.Keys().Finnhub
		if key == "" {
			select {
			case <-ctx.Done():
				return
			case <-time.After(2 * time.Second):
				continue
			}
		}
		m.mu.RLock()
		focus := m.focus
		m.mu.RUnlock()
		var symbol string
		if focus != "" && requestNumber%4 == 0 {
			symbol = focus
		} else {
			symbol = symbols[i%len(symbols)]
			i++
		}
		requestNumber++
		quote, err := m.fetchQuote(ctx, symbol, key)
		if err != nil {
			m.mu.Lock()
			m.restError = redact(err.Error(), key)
			m.mu.Unlock()
		} else {
			m.mu.Lock()
			m.restError = ""
			m.mu.Unlock()
			m.update(quote)
		}
		pause := 2 * time.Second
		if err != nil && strings.Contains(err.Error(), "429") {
			pause = 60 * time.Second
		}
		select {
		case <-ctx.Done():
			return
		case <-time.After(pause):
		}
	}
}

func (m *Market) fetchQuote(ctx context.Context, symbol, key string) (Quote, error) {
	var quote Quote
	request, err := http.NewRequestWithContext(ctx, http.MethodGet, "https://finnhub.io/api/v1/quote?symbol="+url.QueryEscape(symbol), nil)
	if err != nil {
		return quote, err
	}
	request.Header.Set("X-Finnhub-Token", key)
	response, err := m.client.Do(request)
	if err != nil {
		return quote, err
	}
	defer response.Body.Close()
	if response.StatusCode != 200 {
		return quote, fmt.Errorf("Finnhub quote HTTP %d", response.StatusCode)
	}
	var body struct {
		Price    float64 `json:"c"`
		Previous float64 `json:"pc"`
		Time     int64   `json:"t"`
	}
	if err := json.NewDecoder(response.Body).Decode(&body); err != nil {
		return quote, err
	}
	if body.Price <= 0 || body.Time <= 0 {
		return quote, fmt.Errorf("Finnhub has no quote for %s", symbol)
	}
	return Quote{Symbol: symbol, Price: body.Price, PreviousClose: body.Previous,
		TradeAt:    time.Unix(body.Time, 0).UTC().Format(time.RFC3339Nano),
		ObservedAt: time.Now().UTC().Format(time.RFC3339Nano), Source: "Finnhub quote"}, nil
}

func redact(value, key string) string {
	if key != "" {
		value = strings.ReplaceAll(value, key, "[key redacted]")
	}
	if len(value) > 300 {
		value = value[:300]
	}
	return value
}
