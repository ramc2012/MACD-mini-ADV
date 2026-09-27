package main

import (
	"context"
	"fmt"
	"log"
	"math"
	"sync"
	"time"
)

type TradeTick struct {
	Symbol        string
	Price, Volume float64
	At            time.Time
}
type macdPoint struct{ MACD, Signal float64 }
type SpotStatus struct {
	Mode               string  `json:"mode"`
	AutoEnabled        bool    `json:"auto_enabled"`
	ScannerSymbols     int     `json:"scanner_symbols"`
	UniverseSymbols    int     `json:"universe_symbols"`
	SymbolsWithBars    int     `json:"symbols_with_bars"`
	ReadySymbols       int     `json:"ready_symbols"`
	FreshBreadthCohort int     `json:"fresh_breadth_cohort"`
	BreadthPositivePct float64 `json:"breadth_positive_pct"`
	WarmupBars         int     `json:"warmup_bars"`
	MinBars            int     `json:"min_bars"`
	LastClosedBar      string  `json:"last_closed_bar,omitempty"`
	LastDecision       string  `json:"last_decision,omitempty"`
	LastFill           string  `json:"last_fill,omitempty"`
	DroppedTrades      uint64  `json:"dropped_trades"`
	DataNote           string  `json:"data_note"`
}

type SpotEngine struct {
	mu        sync.RWMutex
	market    *Market
	ledger    *Ledger
	bars      map[string][]MinuteBar
	open      map[string]MinuteBar
	risk      map[string]SpotRisk
	positions map[string]Position
	status    SpotStatus
}

func NewSpotEngine(market *Market, ledger *Ledger) (*SpotEngine, error) {
	e := &SpotEngine{market: market, ledger: ledger, bars: map[string][]MinuteBar{}, open: map[string]MinuteBar{}, risk: map[string]SpotRisk{}, positions: map[string]Position{}}
	for _, stock := range stockUniverse() {
		bars, err := ledger.LoadSpotBars(stock.Symbol)
		if err != nil {
			return nil, err
		}
		e.bars[stock.Symbol] = bars
	}
	book, err := ledger.Book(market.Quotes())
	if err != nil {
		return nil, err
	}
	for _, p := range book.Positions {
		e.positions[p.Symbol] = p
		if r, err := ledger.GetSpotRisk(p.Symbol); err == nil {
			e.risk[p.Symbol] = r
		}
	}
	enabled, err := ledger.SpotAutomation()
	if err != nil {
		return nil, err
	}
	e.status = SpotStatus{Mode: "Blast-inspired spot paper", AutoEnabled: enabled, UniverseSymbols: 100, MinBars: 120, DataNote: "Live Finnhub WebSocket trades only; no historical minute access. Option premium and option-side breadth rules are unavailable for stocks."}
	return e, nil
}

func (e *SpotEngine) Status() SpotStatus {
	e.mu.RLock()
	defer e.mu.RUnlock()
	s := e.status
	e.market.mu.RLock()
	s.ScannerSymbols = e.market.wsLimit
	s.DroppedTrades = e.market.droppedTrades
	e.market.mu.RUnlock()
	return s
}
func (e *SpotEngine) SetAuto(enabled bool) error {
	if err := e.ledger.SetSpotAutomation(enabled); err != nil {
		return err
	}
	e.mu.Lock()
	e.status.AutoEnabled = enabled
	e.mu.Unlock()
	return nil
}

func (e *SpotEngine) Run(ctx context.Context) {
	tick := time.NewTicker(time.Second)
	defer tick.Stop()
	for {
		select {
		case <-ctx.Done():
			return
		case event := <-e.market.trades:
			e.onTrade(event)
		case now := <-tick.C:
			e.closeBars(now)
		}
	}
}

func (e *SpotEngine) onTrade(t TradeTick) {
	now := time.Now().UTC()
	if t.Price <= 0 || t.At.After(now.Add(5*time.Second)) || now.Sub(t.At) > 30*time.Second || !marketClock(t.At).Open {
		return
	}
	e.checkExit(t)
	minute := t.At.Truncate(time.Minute)
	bar, exists := e.open[t.Symbol]
	if exists && minute.Before(bar.At) {
		return
	}
	if exists && minute.After(bar.At) {
		e.finishBar(bar, now)
		exists = false
	}
	if !exists {
		e.open[t.Symbol] = MinuteBar{Symbol: t.Symbol, At: minute, Open: t.Price, High: t.Price, Low: t.Price, Close: t.Price, Volume: math.Max(0, t.Volume), Source: "Finnhub WebSocket trade"}
		return
	}
	if t.Price > bar.High {
		bar.High = t.Price
	}
	if t.Price < bar.Low {
		bar.Low = t.Price
	}
	bar.Close = t.Price
	bar.Volume += math.Max(0, t.Volume)
	e.open[t.Symbol] = bar
}

func (e *SpotEngine) closeBars(now time.Time) {
	for symbol, bar := range e.open {
		if !bar.At.Add(time.Minute).After(now) {
			e.finishBar(bar, now)
			delete(e.open, symbol)
		}
	}
	e.refreshCoverage(now)
}

func (e *SpotEngine) finishBar(bar MinuteBar, now time.Time) {
	prior := e.bars[bar.Symbol]
	if len(prior) > 0 && !bar.At.After(prior[len(prior)-1].At) {
		return
	}
	if err := e.ledger.SaveSpotBar(bar); err != nil {
		log.Printf("spot bar storage: %v", err)
		return
	}
	if len(prior) > 999 {
		prior = prior[len(prior)-999:]
	}
	e.bars[bar.Symbol] = append(prior, bar)
	e.mu.Lock()
	e.status.LastClosedBar = bar.At.Add(time.Minute).Format(time.RFC3339)
	e.mu.Unlock()
	e.evaluate(bar, prior, now)
}

func calcMACD(bars []MinuteBar) (macdPoint, macdPoint) {
	if len(bars) == 0 {
		return macdPoint{}, macdPoint{}
	}
	fast, slow, signal := bars[0].Close, bars[0].Close, 0.0
	prev := macdPoint{}
	for i, b := range bars {
		fast += (b.Close - fast) * (2.0 / 13)
		slow += (b.Close - slow) * (2.0 / 27)
		m := fast - slow
		if i == 0 {
			signal = m
		} else {
			signal += (m - signal) * (2.0 / 10)
		}
		if i == len(bars)-2 {
			prev = macdPoint{m, signal}
		}
	}
	return prev, macdPoint{fast - slow, signal}
}

func (e *SpotEngine) breadth(now time.Time) (int, int) {
	cohort, positive := 0, 0
	for _, bars := range e.bars {
		if len(bars) < 120 {
			continue
		}
		last := bars[len(bars)-1]
		if now.Sub(last.At.Add(time.Minute)) > 5*time.Minute {
			continue
		}
		_, point := calcMACD(bars)
		cohort++
		if point.MACD > 0 {
			positive++
		}
	}
	return cohort, positive
}

func (e *SpotEngine) evaluate(bar MinuteBar, prior []MinuteBar, now time.Time) {
	if len(prior) < 120 {
		return
	}
	all := e.bars[bar.Symbol]
	prev, current := calcMACD(all)
	if !(prev.MACD <= prev.Signal && current.MACD > current.Signal) {
		return
	}
	verdict := "TAKEN"
	defer func() {
		e.mu.Lock()
		e.status.LastDecision = fmt.Sprintf("%s %s %s", bar.At.Format("15:04"), bar.Symbol, verdict)
		e.mu.Unlock()
	}()
	if _, held := e.positions[bar.Symbol]; held {
		verdict = "ALREADY_HELD"
		return
	}
	if !e.Status().AutoEnabled {
		verdict = "AUTO_PAUSED"
		return
	}
	if !marketClock(now).Open || now.Sub(bar.At.Add(time.Minute)) > 2*time.Minute {
		verdict = "LATE_OR_CLOSED"
		return
	}
	if e.market.Status().Status != "connected" || e.market.Status().LastTradeAt == "" || e.Status().DroppedTrades > 0 {
		verdict = "FEED_NOT_READY"
		return
	}
	cohort, positive := e.breadth(now)
	if cohort < 20 {
		verdict = "NO_BREADTH"
		return
	}
	if float64(positive)/float64(cohort) < 0.5 {
		verdict = "BREADTH_TOO_THIN"
		return
	}
	start := 0
	if len(prior) > 750 {
		start = len(prior) - 750
	}
	high := 0.0
	for _, b := range prior[start:] {
		if b.High > high {
			high = b.High
		}
	}
	if high <= 0 || bar.Close > high*0.75 {
		verdict = "NOT_OFF_HIGH"
		return
	}
	if len(e.positions) >= 10 {
		verdict = "POSITION_LIMIT"
		return
	}
	quote, ok := e.market.TradeQuote(bar.Symbol)
	if !ok || quote.Source != "Finnhub trade" {
		verdict = "NO_FRESH_TRADE"
		return
	}
	tradeAt, err := time.Parse(time.RFC3339Nano, quote.TradeAt)
	if err != nil || now.Sub(tradeAt) > 30*time.Second || tradeAt.After(now.Add(5*time.Second)) {
		verdict = "STALE_PRICE"
		return
	}
	book, err := e.ledger.Book(e.market.Quotes())
	if err != nil {
		verdict = "LEDGER_ERROR"
		return
	}
	target := math.Min(100000, math.Min(book.Account.Cash, book.Account.Equity*0.1))
	qty := int64(math.Floor(target / quote.Price))
	liquid := int64(math.Floor(bar.Volume * 0.02))
	if qty > liquid {
		qty = liquid
	}
	if qty < 1 {
		verdict = "TOO_ILLIQUID"
		return
	}
	if err = e.ledger.SaveSpotRisk(bar.Symbol, SpotRisk{Peak: quote.Price}); err != nil {
		verdict = "RISK_STORAGE_ERROR"
		return
	}
	fill, err := e.ledger.Place(bar.Symbol, "BUY", qty, quote, e.market.Quotes())
	if err != nil {
		_ = e.ledger.DeleteSpotRisk(bar.Symbol)
		verdict = "ENTRY_REJECTED: " + err.Error()
		return
	}
	e.risk[bar.Symbol] = SpotRisk{Peak: fill.Price}
	e.positions[bar.Symbol] = Position{Symbol: bar.Symbol, Quantity: qty, AverageCost: fill.Price}
	if next, err := e.ledger.Book(e.market.Quotes()); err == nil {
		_ = e.ledger.RecordEquity(next.Account)
	}
	e.mu.Lock()
	e.status.LastFill = fmt.Sprintf("BUY %s × %d at $%.2f", bar.Symbol, qty, fill.Price)
	e.mu.Unlock()
}

func (e *SpotEngine) checkExit(t TradeTick) {
	p, held := e.positions[t.Symbol]
	if !held {
		return
	}
	r, managed := e.risk[t.Symbol]
	if !managed {
		return
	}
	changed := false
	if t.Price > r.Peak {
		r.Peak = t.Price
		changed = true
	}
	if !r.Armed && r.Peak >= p.AverageCost*1.30 {
		r.Armed = true
		changed = true
	}
	if changed {
		if err := e.ledger.SaveSpotRisk(t.Symbol, r); err != nil {
			log.Printf("spot risk storage: %v", err)
			return
		}
		e.risk[t.Symbol] = r
	}
	stop := p.AverageCost * 0.5
	if r.Armed {
		trail := math.Max(p.AverageCost, r.Peak*0.6)
		if trail > stop {
			stop = trail
		}
	}
	if t.Price > stop {
		return
	}
	q := Quote{Symbol: t.Symbol, Price: t.Price, TradeAt: t.At.Format(time.RFC3339Nano), ObservedAt: time.Now().UTC().Format(time.RFC3339Nano), Source: "Finnhub trade"}
	fill, err := e.ledger.Place(t.Symbol, "SELL", p.Quantity, q, e.market.Quotes())
	if err != nil {
		log.Printf("spot exit %s: %v", t.Symbol, err)
		return
	}
	delete(e.positions, t.Symbol)
	delete(e.risk, t.Symbol)
	_ = e.ledger.DeleteSpotRisk(t.Symbol)
	if book, err := e.ledger.Book(e.market.Quotes()); err == nil {
		_ = e.ledger.RecordEquity(book.Account)
	}
	e.mu.Lock()
	e.status.LastFill = fmt.Sprintf("SELL %s × %d at $%.2f", t.Symbol, p.Quantity, fill.Price)
	e.mu.Unlock()
}

func (e *SpotEngine) refreshCoverage(now time.Time) {
	symbols, ready := 0, 0
	for _, bars := range e.bars {
		if len(bars) > 0 {
			symbols++
		}
		if len(bars) >= 120 {
			ready++
		}
	}
	cohort, positive := e.breadth(now)
	warmup := 0
	for _, bars := range e.bars {
		if len(bars) > warmup {
			warmup = len(bars)
		}
	}
	e.mu.Lock()
	e.status.SymbolsWithBars = symbols
	e.status.ReadySymbols = ready
	e.status.FreshBreadthCohort = cohort
	e.status.WarmupBars = warmup
	if cohort > 0 {
		e.status.BreadthPositivePct = float64(positive) / float64(cohort) * 100
	} else {
		e.status.BreadthPositivePct = 0
	}
	e.mu.Unlock()
}
