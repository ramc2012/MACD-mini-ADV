package main

import (
	"database/sql"
	"errors"
	"fmt"
	"math"
	"os"
	"path/filepath"
	"sync"
	"time"

	_ "modernc.org/sqlite"
)

const initialCapitalCents int64 = 100_000_000 // $1,000,000.00
const maxPositionWeight = 0.10

type Ledger struct {
	db *sql.DB
	mu sync.Mutex
}

type Position struct {
	Symbol        string  `json:"symbol"`
	Quantity      int64   `json:"quantity"`
	AverageCost   float64 `json:"average_cost"`
	MarketPrice   float64 `json:"market_price"`
	MarketValue   float64 `json:"market_value"`
	Unrealized    float64 `json:"unrealized"`
	MarkEstimated bool    `json:"mark_estimated"`
}

type Fill struct {
	ID       int64   `json:"id"`
	At       string  `json:"at"`
	Symbol   string  `json:"symbol"`
	Side     string  `json:"side"`
	Quantity int64   `json:"quantity"`
	Price    float64 `json:"price"`
	Notional float64 `json:"notional"`
	Realized float64 `json:"realized"`
	Source   string  `json:"source"`
}

type Account struct {
	InitialCapital float64 `json:"initial_capital"`
	Cash           float64 `json:"cash"`
	Equity         float64 `json:"equity"`
	MarketValue    float64 `json:"market_value"`
	Realized       float64 `json:"realized"`
	Unrealized     float64 `json:"unrealized"`
	ReturnPct      float64 `json:"return_pct"`
	Estimated      bool    `json:"estimated"`
}

type EquityPoint struct {
	At     string  `json:"at"`
	Equity float64 `json:"equity"`
}

type PaperBook struct {
	Account       Account       `json:"account"`
	Positions     []Position    `json:"positions"`
	Fills         []Fill        `json:"fills"`
	EquityHistory []EquityPoint `json:"equity_history"`
}

func usd(cents int64) float64   { return float64(cents) / 100 }
func cents(value float64) int64 { return int64(math.Round(value * 100)) }

func NewLedger(path string) (*Ledger, error) {
	if err := os.MkdirAll(filepath.Dir(path), 0700); err != nil {
		return nil, err
	}
	db, err := sql.Open("sqlite", path)
	if err != nil {
		return nil, err
	}
	db.SetMaxOpenConns(1)
	for _, statement := range []string{
		"PRAGMA busy_timeout=5000",
		"PRAGMA journal_mode=DELETE",
		`CREATE TABLE IF NOT EXISTS account (id INTEGER PRIMARY KEY CHECK(id=1), cash_cents INTEGER NOT NULL, realized_cents INTEGER NOT NULL)`,
		`INSERT OR IGNORE INTO account(id,cash_cents,realized_cents) VALUES (1,100000000,0)`,
		`CREATE TABLE IF NOT EXISTS positions (symbol TEXT PRIMARY KEY, quantity INTEGER NOT NULL CHECK(quantity>0), cost_basis_cents INTEGER NOT NULL)`,
		`CREATE TABLE IF NOT EXISTS fills (id INTEGER PRIMARY KEY AUTOINCREMENT, at TEXT NOT NULL, symbol TEXT NOT NULL, side TEXT NOT NULL, quantity INTEGER NOT NULL, price_cents INTEGER NOT NULL, notional_cents INTEGER NOT NULL, realized_cents INTEGER NOT NULL, source TEXT NOT NULL)`,
		`CREATE TABLE IF NOT EXISTS equity_snapshots (at TEXT PRIMARY KEY, equity_cents INTEGER NOT NULL)`,
		`CREATE TABLE IF NOT EXISTS daily_history (symbol TEXT NOT NULL, day TEXT NOT NULL, open REAL NOT NULL, high REAL NOT NULL, low REAL NOT NULL, close REAL NOT NULL, volume INTEGER NOT NULL, fetched_at TEXT NOT NULL, PRIMARY KEY(symbol,day))`,
		`CREATE TABLE IF NOT EXISTS spot_minute_bars (symbol TEXT NOT NULL, at TEXT NOT NULL, open REAL NOT NULL, high REAL NOT NULL, low REAL NOT NULL, close REAL NOT NULL, volume REAL NOT NULL, PRIMARY KEY(symbol,at))`,
		`CREATE TABLE IF NOT EXISTS spot_risk (symbol TEXT PRIMARY KEY, peak REAL NOT NULL, armed INTEGER NOT NULL)`,
		`CREATE TABLE IF NOT EXISTS spot_settings (id INTEGER PRIMARY KEY CHECK(id=1), auto_enabled INTEGER NOT NULL)`,
		`INSERT OR IGNORE INTO spot_settings (id,auto_enabled) VALUES (1,1)`,
	} {
		if _, err = db.Exec(statement); err != nil {
			db.Close()
			return nil, err
		}
	}
	return &Ledger{db: db}, nil
}

func (l *Ledger) Close() error { return l.db.Close() }

func (l *Ledger) Book(quotes map[string]Quote) (PaperBook, error) {
	l.mu.Lock()
	defer l.mu.Unlock()
	book := PaperBook{Positions: []Position{}, Fills: []Fill{}, EquityHistory: []EquityPoint{}}
	var cash, realized int64
	if err := l.db.QueryRow("SELECT cash_cents,realized_cents FROM account WHERE id=1").Scan(&cash, &realized); err != nil {
		return book, err
	}
	book.Account = Account{InitialCapital: usd(initialCapitalCents), Cash: usd(cash), Realized: usd(realized)}
	rows, err := l.db.Query("SELECT symbol,quantity,cost_basis_cents FROM positions ORDER BY symbol")
	if err != nil {
		return book, err
	}
	for rows.Next() {
		var symbol string
		var quantity, basis int64
		if err := rows.Scan(&symbol, &quantity, &basis); err != nil {
			rows.Close()
			return book, err
		}
		mark, ok := quotes[symbol]
		markCents := cents(mark.Price)
		estimated := !ok || markCents <= 0
		if estimated {
			markCents = int64(math.Round(float64(basis) / float64(quantity)))
		}
		value := markCents * quantity
		book.Account.MarketValue += usd(value)
		book.Account.Unrealized += usd(value - basis)
		book.Account.Estimated = book.Account.Estimated || estimated
		book.Positions = append(book.Positions, Position{symbol, quantity, usd(basis) / float64(quantity), usd(markCents), usd(value), usd(value - basis), estimated})
	}
	if err := rows.Err(); err != nil {
		rows.Close()
		return book, err
	}
	rows.Close()
	book.Account.Equity = book.Account.Cash + book.Account.MarketValue
	book.Account.ReturnPct = (book.Account.Equity/book.Account.InitialCapital - 1) * 100
	rows, err = l.db.Query("SELECT id,at,symbol,side,quantity,price_cents,notional_cents,realized_cents,source FROM fills ORDER BY id DESC LIMIT 100")
	if err != nil {
		return book, err
	}
	for rows.Next() {
		var row Fill
		var price, notional, profit int64
		if err := rows.Scan(&row.ID, &row.At, &row.Symbol, &row.Side, &row.Quantity, &price, &notional, &profit, &row.Source); err != nil {
			rows.Close()
			return book, err
		}
		row.Price, row.Notional, row.Realized = usd(price), usd(notional), usd(profit)
		book.Fills = append(book.Fills, row)
	}
	if err := rows.Err(); err != nil {
		rows.Close()
		return book, err
	}
	rows.Close()
	rows, err = l.db.Query("SELECT at,equity_cents FROM equity_snapshots ORDER BY at DESC LIMIT 300")
	if err != nil {
		return book, err
	}
	for rows.Next() {
		var at string
		var equity int64
		if err := rows.Scan(&at, &equity); err != nil {
			rows.Close()
			return book, err
		}
		book.EquityHistory = append(book.EquityHistory, EquityPoint{at, usd(equity)})
	}
	if err := rows.Err(); err != nil {
		rows.Close()
		return book, err
	}
	rows.Close()
	return book, nil
}

func (l *Ledger) Place(symbol, side string, quantity int64, quote Quote, quotes map[string]Quote) (Fill, error) {
	l.mu.Lock()
	defer l.mu.Unlock()
	var fill Fill
	price := cents(quote.Price)
	if quantity < 1 || quantity > 100000 || price <= 0 {
		return fill, errors.New("invalid quantity or price")
	}
	if quantity > math.MaxInt64/price {
		return fill, errors.New("order exceeds numeric limit")
	}
	notional := quantity * price
	tx, err := l.db.Begin()
	if err != nil {
		return fill, err
	}
	defer tx.Rollback()
	var cash, realized int64
	if err := tx.QueryRow("SELECT cash_cents,realized_cents FROM account WHERE id=1").Scan(&cash, &realized); err != nil {
		return fill, err
	}
	var held, basis int64
	err = tx.QueryRow("SELECT quantity,cost_basis_cents FROM positions WHERE symbol=?", symbol).Scan(&held, &basis)
	if err != nil && err != sql.ErrNoRows {
		return fill, err
	}
	if side == "BUY" {
		if notional > cash {
			return fill, errors.New("insufficient paper cash; margin is disabled")
		}
		equity := cash
		rows, err := tx.Query("SELECT symbol,quantity,cost_basis_cents FROM positions")
		if err != nil {
			return fill, err
		}
		for rows.Next() {
			var heldSymbol string
			var heldQuantity, heldBasis int64
			if err := rows.Scan(&heldSymbol, &heldQuantity, &heldBasis); err != nil {
				rows.Close()
				return fill, err
			}
			mark := cents(quotes[heldSymbol].Price)
			if mark <= 0 {
				mark = int64(math.Round(float64(heldBasis) / float64(heldQuantity)))
			}
			equity += heldQuantity * mark
		}
		if err := rows.Err(); err != nil {
			rows.Close()
			return fill, err
		}
		rows.Close()
		if float64((held+quantity)*price) > float64(equity)*maxPositionWeight+0.5 {
			return fill, fmt.Errorf("single-stock limit is %.0f%% of account equity", maxPositionWeight*100)
		}
		cash -= notional
		_, err = tx.Exec("INSERT INTO positions(symbol,quantity,cost_basis_cents) VALUES(?,?,?) ON CONFLICT(symbol) DO UPDATE SET quantity=quantity+excluded.quantity,cost_basis_cents=cost_basis_cents+excluded.cost_basis_cents", symbol, quantity, notional)
		if err != nil {
			return fill, err
		}
	} else if side == "SELL" {
		if quantity > held {
			return fill, errors.New("cannot sell more shares than held; shorting is disabled")
		}
		removed := int64(math.Round(float64(basis) * float64(quantity) / float64(held)))
		profit := notional - removed
		realized += profit
		cash += notional
		if quantity == held {
			_, err = tx.Exec("DELETE FROM positions WHERE symbol=?", symbol)
		} else {
			_, err = tx.Exec("UPDATE positions SET quantity=?,cost_basis_cents=? WHERE symbol=?", held-quantity, basis-removed, symbol)
		}
		if err != nil {
			return fill, err
		}
		fill.Realized = usd(profit)
	} else {
		return fill, errors.New("side must be BUY or SELL")
	}
	if _, err = tx.Exec("UPDATE account SET cash_cents=?,realized_cents=? WHERE id=1", cash, realized); err != nil {
		return fill, err
	}
	fill.At = time.Now().UTC().Format(time.RFC3339Nano)
	result, err := tx.Exec("INSERT INTO fills(at,symbol,side,quantity,price_cents,notional_cents,realized_cents,source) VALUES(?,?,?,?,?,?,?,?)", fill.At, symbol, side, quantity, price, notional, cents(fill.Realized), quote.Source)
	if err != nil {
		return fill, err
	}
	fill.ID, err = result.LastInsertId()
	if err != nil {
		return fill, err
	}
	fill.Symbol, fill.Side, fill.Quantity, fill.Price, fill.Notional, fill.Source = symbol, side, quantity, usd(price), usd(notional), quote.Source
	if err = tx.Commit(); err != nil {
		return fill, err
	}
	return fill, nil
}
