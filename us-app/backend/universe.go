package main

import "strings"

// A deliberately fixed, diversified 100-stock watchlist. Index trackers are
// separate market gauges and can never enter the stock paper order book.
var universeGroups = []struct {
	Sector  string
	Symbols string
}{
	{"Technology & growth", "AAPL MSFT NVDA AMZN GOOGL META TSLA AVGO ORCL CRM ADBE NFLX AMD INTC QCOM TXN AMAT MU LRCX KLAC ADI NOW INTU PANW CRWD SNOW PLTR IBM CSCO ANET"},
	{"Consumer & communications", "UBER ABNB BKNG MAR F GM RIVN DIS CMCSA T VZ TMUS KO PEP PG COST WMT TGT HD LOW MCD SBUX NKE PM"},
	{"Financials", "V MA AXP JPM BAC WFC C GS MS BLK SCHW SPGI"},
	{"Industrials", "GEV GE CAT DE BA RTX LMT HON MMM ETN EMR PH UNP UPS FDX"},
	{"Healthcare", "UNH JNJ PFE MRK ABBV LLY AMGN GILD REGN TMO DHR ISRG MDT"},
	{"Energy & utilities", "XOM CVX COP SLB NEE SO"},
}

type Instrument struct {
	Symbol string `json:"symbol"`
	Sector string `json:"sector"`
}

type IndexGauge struct {
	Name   string `json:"name"`
	Symbol string `json:"symbol"`
	Note   string `json:"note"`
}

var indexGauges = []IndexGauge{
	{"S&P 500", "SPY", "ETF proxy; not the index level"},
	{"Nasdaq 100", "QQQ", "ETF proxy; not the index level"},
	{"Dow Jones", "DIA", "ETF proxy; not the index level"},
	{"Russell 2000", "IWM", "ETF proxy; not the index level"},
}

func stockUniverse() []Instrument {
	rows := make([]Instrument, 0, 100)
	for _, group := range universeGroups {
		for _, symbol := range strings.Fields(group.Symbols) {
			rows = append(rows, Instrument{Symbol: symbol, Sector: group.Sector})
		}
	}
	return rows
}

func stockSet() map[string]bool {
	set := make(map[string]bool, 100)
	for _, row := range stockUniverse() {
		set[row.Symbol] = true
	}
	return set
}
