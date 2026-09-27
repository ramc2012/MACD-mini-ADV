package main

import (
	"math"
	"path/filepath"
	"testing"
	"time"
)

func TestUniverseHasExactly100UniqueStocks(t *testing.T) {
	rows := stockUniverse()
	if len(rows) != 100 || len(stockSet()) != 100 {
		t.Fatalf("want 100 unique stocks, got rows=%d unique=%d", len(rows), len(stockSet()))
	}
	for _, gauge := range indexGauges {
		if stockSet()[gauge.Symbol] {
			t.Fatalf("index gauge %s must not be tradable", gauge.Symbol)
		}
	}
}

func TestPaperLedgerCashPositionLimitAndRealizedProfit(t *testing.T) {
	ledger, err := NewLedger(filepath.Join(t.TempDir(), "paper.sqlite3"))
	if err != nil {
		t.Fatal(err)
	}
	defer ledger.Close()
	quote := Quote{Symbol: "AAPL", Price: 100, Source: "Finnhub trade"}
	quotes := map[string]Quote{"AAPL": quote}
	if _, err := ledger.Place("AAPL", "BUY", 1000, quote, quotes); err != nil {
		t.Fatal(err)
	}
	if _, err := ledger.Place("AAPL", "BUY", 1, quote, quotes); err == nil {
		t.Fatal("10% limit was bypassed")
	}
	if _, err := ledger.Place("AAPL", "SELL", 1001, quote, quotes); err == nil {
		t.Fatal("short sale was allowed")
	}
	book, err := ledger.Book(quotes)
	if err != nil {
		t.Fatal(err)
	}
	if book.Account.Cash != 900000 || book.Account.Equity != 1000000 || len(book.Positions) != 1 {
		t.Fatalf("unexpected book after buy: %+v", book.Account)
	}
	quote.Price = 110
	quotes["AAPL"] = quote
	fill, err := ledger.Place("AAPL", "SELL", 1000, quote, quotes)
	if err != nil {
		t.Fatal(err)
	}
	if fill.Realized != 10000 {
		t.Fatalf("realized profit = %v", fill.Realized)
	}
	book, err = ledger.Book(quotes)
	if err != nil {
		t.Fatal(err)
	}
	if math.Abs(book.Account.Cash-1010000) > 0.001 || book.Account.Realized != 10000 || len(book.Positions) != 0 {
		t.Fatalf("unexpected book after sell: %+v", book.Account)
	}
}

func TestRegularSessionWindow(t *testing.T) {
	location, err := time.LoadLocation("America/New_York")
	if err != nil {
		t.Fatal(err)
	}
	date := func(hour, minute int) time.Time { return time.Date(2026, 9, 25, hour, minute, 0, 0, location) }
	if marketClock(date(9, 29)).Open || !marketClock(date(9, 30)).Open || !marketClock(date(15, 59)).Open || marketClock(date(16, 0)).Open {
		t.Fatal("incorrect regular market session boundaries")
	}
}
