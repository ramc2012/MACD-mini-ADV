package main

import (
	"database/sql"
	"time"
)

type SpotRisk struct {
	Peak  float64
	Armed bool
}

func (l *Ledger) SaveSpotBar(bar MinuteBar) error {
	l.mu.Lock()
	defer l.mu.Unlock()
	_, err := l.db.Exec(`INSERT OR IGNORE INTO spot_minute_bars(symbol,at,open,high,low,close,volume) VALUES(?,?,?,?,?,?,?)`, bar.Symbol, bar.At.UTC().Format(time.RFC3339), bar.Open, bar.High, bar.Low, bar.Close, bar.Volume)
	return err
}

func (l *Ledger) LoadSpotBars(symbol string) ([]MinuteBar, error) {
	rows, err := l.db.Query(`SELECT at,open,high,low,close,volume FROM (SELECT at,open,high,low,close,volume FROM spot_minute_bars WHERE symbol=? ORDER BY at DESC LIMIT 1000) ORDER BY at`, symbol)
	if err != nil {
		return nil, err
	}
	defer rows.Close()
	bars := []MinuteBar{}
	for rows.Next() {
		var at string
		var b MinuteBar
		b.Symbol = symbol
		b.Source = "Finnhub WebSocket trade"
		if err = rows.Scan(&at, &b.Open, &b.High, &b.Low, &b.Close, &b.Volume); err != nil {
			return nil, err
		}
		b.At, err = time.Parse(time.RFC3339, at)
		if err != nil {
			return nil, err
		}
		bars = append(bars, b)
	}
	return bars, rows.Err()
}

func (l *Ledger) SpotAutomation() (bool, error) {
	var enabled int
	err := l.db.QueryRow(`SELECT auto_enabled FROM spot_settings WHERE id=1`).Scan(&enabled)
	return enabled == 1, err
}
func (l *Ledger) SetSpotAutomation(enabled bool) error {
	n := 0
	if enabled {
		n = 1
	}
	_, err := l.db.Exec(`UPDATE spot_settings SET auto_enabled=? WHERE id=1`, n)
	return err
}

func (l *Ledger) GetSpotRisk(symbol string) (SpotRisk, error) {
	var r SpotRisk
	var armed int
	err := l.db.QueryRow(`SELECT peak,armed FROM spot_risk WHERE symbol=?`, symbol).Scan(&r.Peak, &armed)
	r.Armed = armed == 1
	return r, err
}
func (l *Ledger) SaveSpotRisk(symbol string, r SpotRisk) error {
	armed := 0
	if r.Armed {
		armed = 1
	}
	_, err := l.db.Exec(`INSERT INTO spot_risk(symbol,peak,armed) VALUES(?,?,?) ON CONFLICT(symbol) DO UPDATE SET peak=excluded.peak,armed=excluded.armed`, symbol, r.Peak, armed)
	return err
}
func (l *Ledger) DeleteSpotRisk(symbol string) error {
	_, err := l.db.Exec(`DELETE FROM spot_risk WHERE symbol=?`, symbol)
	return err
}
func (l *Ledger) SpotRiskExists(symbol string) bool {
	_, err := l.GetSpotRisk(symbol)
	return err != sql.ErrNoRows && err == nil
}
