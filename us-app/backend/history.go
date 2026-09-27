package main

import (
	"context"
	"encoding/json"
	"errors"
	"fmt"
	"net/http"
	"net/url"
	"sort"
	"strconv"
	"time"
)

type DailyBar struct {
	Day    string  `json:"day"`
	Open   float64 `json:"open"`
	High   float64 `json:"high"`
	Low    float64 `json:"low"`
	Close  float64 `json:"close"`
	Volume int64   `json:"volume"`
}

type DailyHistory struct {
	Symbol    string     `json:"symbol"`
	Source    string     `json:"source"`
	FetchedAt string     `json:"fetched_at"`
	Bars      []DailyBar `json:"bars"`
}

func (l *Ledger) CachedHistory(symbol string) (DailyHistory, error) {
	l.mu.Lock()
	defer l.mu.Unlock()
	history := DailyHistory{Symbol: symbol, Source: "Alpha Vantage daily", Bars: []DailyBar{}}
	rows, err := l.db.Query("SELECT day,open,high,low,close,volume,fetched_at FROM daily_history WHERE symbol=? ORDER BY day DESC LIMIT 100", symbol)
	if err != nil {
		return history, err
	}
	for rows.Next() {
		var bar DailyBar
		var fetched string
		if err := rows.Scan(&bar.Day, &bar.Open, &bar.High, &bar.Low, &bar.Close, &bar.Volume, &fetched); err != nil {
			rows.Close()
			return history, err
		}
		if fetched > history.FetchedAt {
			history.FetchedAt = fetched
		}
		history.Bars = append(history.Bars, bar)
	}
	if err := rows.Err(); err != nil {
		rows.Close()
		return history, err
	}
	rows.Close()
	for i, j := 0, len(history.Bars)-1; i < j; i, j = i+1, j-1 {
		history.Bars[i], history.Bars[j] = history.Bars[j], history.Bars[i]
	}
	return history, nil
}

func (l *Ledger) SaveHistory(symbol string, bars []DailyBar) error {
	l.mu.Lock()
	defer l.mu.Unlock()
	tx, err := l.db.Begin()
	if err != nil {
		return err
	}
	defer tx.Rollback()
	now := time.Now().UTC().Format(time.RFC3339Nano)
	for _, bar := range bars {
		_, err = tx.Exec(`INSERT INTO daily_history(symbol,day,open,high,low,close,volume,fetched_at)
			VALUES(?,?,?,?,?,?,?,?) ON CONFLICT(symbol,day) DO UPDATE SET
			open=excluded.open,high=excluded.high,low=excluded.low,close=excluded.close,
			volume=excluded.volume,fetched_at=excluded.fetched_at`,
			symbol, bar.Day, bar.Open, bar.High, bar.Low, bar.Close, bar.Volume, now)
		if err != nil {
			return err
		}
	}
	return tx.Commit()
}

func (m *Market) FetchDaily(ctx context.Context, symbol string) ([]DailyBar, error) {
	key := m.Keys().AlphaVantage
	if key == "" {
		return nil, errors.New("save an Alpha Vantage key to load daily history")
	}
	endpoint := "https://www.alphavantage.co/query?function=TIME_SERIES_DAILY&outputsize=compact&symbol=" + url.QueryEscape(symbol) + "&apikey=" + url.QueryEscape(key)
	request, err := http.NewRequestWithContext(ctx, http.MethodGet, endpoint, nil)
	if err != nil {
		return nil, err
	}
	response, err := m.client.Do(request)
	if err != nil {
		return nil, errors.New(redact(err.Error(), key))
	}
	defer response.Body.Close()
	if response.StatusCode != http.StatusOK {
		return nil, fmt.Errorf("Alpha Vantage HTTP %d", response.StatusCode)
	}
	var body map[string]json.RawMessage
	if err = json.NewDecoder(response.Body).Decode(&body); err != nil {
		return nil, err
	}
	for _, field := range []string{"Note", "Information", "Error Message"} {
		if raw := body[field]; len(raw) > 0 {
			var message string
			_ = json.Unmarshal(raw, &message)
			if message == "" {
				message = field
			}
			return nil, errors.New(redact(message, key))
		}
	}
	var series map[string]map[string]string
	if err = json.Unmarshal(body["Time Series (Daily)"], &series); err != nil || len(series) == 0 {
		return nil, errors.New("Alpha Vantage returned no daily candles for this symbol")
	}
	bars := make([]DailyBar, 0, len(series))
	for day, fields := range series {
		open, e1 := strconv.ParseFloat(fields["1. open"], 64)
		high, e2 := strconv.ParseFloat(fields["2. high"], 64)
		low, e3 := strconv.ParseFloat(fields["3. low"], 64)
		closePrice, e4 := strconv.ParseFloat(fields["4. close"], 64)
		volume, e5 := strconv.ParseInt(fields["5. volume"], 10, 64)
		if e1 != nil || e2 != nil || e3 != nil || e4 != nil || e5 != nil || closePrice <= 0 {
			continue
		}
		bars = append(bars, DailyBar{day, open, high, low, closePrice, volume})
	}
	sort.Slice(bars, func(i, j int) bool { return bars[i].Day < bars[j].Day })
	if len(bars) == 0 {
		return nil, errors.New("Alpha Vantage returned invalid daily candles")
	}
	return bars, nil
}
