package main

import (
	"path/filepath"
	"testing"
	"time"
)

func TestSpotBarsAndAutomationPersist(t *testing.T) {
	path := filepath.Join(t.TempDir(), "paper.sqlite3")
	ledger, err := NewLedger(path)
	if err != nil {
		t.Fatal(err)
	}
	market := NewMarket(filepath.Join(t.TempDir(), "keys.json"))
	engine, err := NewSpotEngine(market, ledger)
	if err != nil {
		t.Fatal(err)
	}
	if !engine.Status().AutoEnabled {
		t.Fatal("user-authorized paper automation should start enabled")
	}
	if err = engine.SetAuto(false); err != nil {
		t.Fatal(err)
	}
	bar := MinuteBar{Symbol: "AAPL", At: time.Date(2026, 9, 25, 14, 0, 0, 0, time.UTC), Open: 100, High: 101, Low: 99, Close: 100.5, Volume: 5000}
	engine.finishBar(bar, bar.At.Add(time.Minute))
	book, err := ledger.Book(nil)
	if err != nil {
		t.Fatal(err)
	}
	if len(book.Fills) != 0 {
		t.Fatal("warmup bar caused a paper fill")
	}
	ledger.Close()
	ledger, err = NewLedger(path)
	if err != nil {
		t.Fatal(err)
	}
	defer ledger.Close()
	engine, err = NewSpotEngine(market, ledger)
	if err != nil {
		t.Fatal(err)
	}
	if engine.Status().AutoEnabled || len(engine.bars["AAPL"]) != 1 {
		t.Fatal("spot settings or minute bars did not persist")
	}
}

func TestSpotHardStopClosesManagedPaperPosition(t *testing.T) {
	ledger, err := NewLedger(filepath.Join(t.TempDir(), "paper.sqlite3"))
	if err != nil {
		t.Fatal(err)
	}
	defer ledger.Close()
	market := NewMarket(filepath.Join(t.TempDir(), "keys.json"))
	buy := Quote{Symbol: "AAPL", Price: 100, Source: "Finnhub trade"}
	if _, err = ledger.Place("AAPL", "BUY", 100, buy, map[string]Quote{"AAPL": buy}); err != nil {
		t.Fatal(err)
	}
	if err = ledger.SaveSpotRisk("AAPL", SpotRisk{Peak: 100}); err != nil {
		t.Fatal(err)
	}
	engine, err := NewSpotEngine(market, ledger)
	if err != nil {
		t.Fatal(err)
	}
	engine.checkExit(TradeTick{Symbol: "AAPL", Price: 49, Volume: 1000, At: time.Now()})
	book, err := ledger.Book(map[string]Quote{"AAPL": {Symbol: "AAPL", Price: 49}})
	if err != nil {
		t.Fatal(err)
	}
	if len(book.Positions) != 0 || len(book.Fills) != 2 || book.Fills[0].Side != "SELL" {
		t.Fatal("managed hard stop did not close the position")
	}
}

func TestSpotSignalAutomaticallyBuysOnlyAfterFullScreen(t *testing.T) {
    ledger,err:=NewLedger(filepath.Join(t.TempDir(),"paper.sqlite3"));if err!=nil{t.Fatal(err)};defer ledger.Close()
    market:=NewMarket(filepath.Join(t.TempDir(),"keys.json"))
    engine,err:=NewSpotEngine(market,ledger);if err!=nil{t.Fatal(err)}
    now:=time.Date(2026,9,25,15,57,5,0,time.UTC)
    lastMinute:=now.Truncate(time.Minute).Add(-time.Minute)
    universe:=stockUniverse()
    for _,stock:=range universe[1:21] {
        bars:=make([]MinuteBar,121)
        for i:=range bars {price:=100+float64(i)*0.1;bars[i]=MinuteBar{Symbol:stock.Symbol,At:lastMinute.Add(time.Duration(i-120)*time.Minute),Open:price,High:price,Low:price,Close:price,Volume:10000}}
        engine.bars[stock.Symbol]=bars
    }
    prior:=make([]MinuteBar,120)
    for i:=range prior {price:=100.0;if i>=90{price=100-40*float64(i-89)/30};prior[i]=MinuteBar{Symbol:"AAPL",At:lastMinute.Add(time.Duration(i-120)*time.Minute),Open:price,High:price,Low:price,Close:price,Volume:10000}}
    bar:=MinuteBar{Symbol:"AAPL",At:lastMinute,Open:75,High:75,Low:75,Close:75,Volume:10000}
    engine.bars["AAPL"]=append(prior,bar)
    q:=Quote{Symbol:"AAPL",Price:75,TradeAt:now.Format(time.RFC3339Nano),ObservedAt:now.Format(time.RFC3339Nano),Source:"Finnhub trade"}
    market.update(q);market.setStatus("connected","")
    engine.evaluate(bar,prior,now)
    book,err:=ledger.Book(map[string]Quote{"AAPL":q});if err!=nil{t.Fatal(err)}
    if len(book.Fills)!=1||book.Fills[0].Side!="BUY"||book.Fills[0].Quantity!=200 {t.Fatalf("screen did not submit the expected liquidity-capped paper buy: %+v",book.Fills)}
}
