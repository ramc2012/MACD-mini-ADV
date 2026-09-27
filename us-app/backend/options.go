package main

import (
	"context"
	"encoding/json"
	"errors"
	"fmt"
	"net/http"
	"net/url"
	"strings"
	"time"
)

type OptionContract struct {
	ID           string `json:"contractID"`
	Symbol       string `json:"symbol"`
	Expiration   string `json:"expiration"`
	Strike       string `json:"strike"`
	Type         string `json:"type"`
	Last         string `json:"last"`
	Mark         string `json:"mark"`
	Bid          string `json:"bid"`
	Ask          string `json:"ask"`
	Volume       string `json:"volume"`
	OpenInterest string `json:"open_interest"`
	Date         string `json:"date"`
}

type OptionsCapability struct {
	Available bool   `json:"available"`
	Provider  string `json:"provider"`
	Contracts int    `json:"contracts"`
	Reason    string `json:"reason,omitempty"`
	CheckedAt string `json:"checked_at"`
}

func (m *Market) FetchOptions(ctx context.Context, symbol string) ([]OptionContract, error) {
	key := m.Keys().AlphaVantage
	if key == "" {
		return nil, errors.New("Alpha Vantage key is not configured")
	}
	if !stockSet()[symbol] {
		return nil, errors.New("symbol is outside the US stock universe")
	}
	endpoint := "https://www.alphavantage.co/query?function=REALTIME_OPTIONS&symbol=" + url.QueryEscape(symbol) + "&apikey=" + url.QueryEscape(key)
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
		return nil, fmt.Errorf("Alpha Vantage options HTTP %d", response.StatusCode)
	}
	var body struct {
		Data        []OptionContract `json:"data"`
		Message     string           `json:"message"`
		Information string           `json:"Information"`
		Note        string           `json:"Note"`
		Error       string           `json:"Error Message"`
	}
	if err = json.NewDecoder(response.Body).Decode(&body); err != nil {
		return nil, err
	}
	for _, message := range []string{body.Information, body.Note, body.Error, body.Message} {
		if message != "" && (strings.Contains(strings.ToLower(message), "premium") || strings.Contains(strings.ToLower(message), "rate limit") || strings.Contains(strings.ToLower(message), "artificial") || body.Error != "") {
			return nil, errors.New(redact(message, key))
		}
	}
	valid := make([]OptionContract, 0, len(body.Data))
	for _, contract := range body.Data {
		if contract.Symbol == symbol && contract.ID != "" && !strings.HasPrefix(contract.ID, "XXYYZZ") && (contract.Type == "call" || contract.Type == "put") {
			valid = append(valid, contract)
		}
	}
	if len(valid) == 0 {
		return nil, errors.New("Alpha Vantage returned no valid realtime option contracts")
	}
	return valid, nil
}

func (m *Market) ProbeOptions(ctx context.Context,force bool) OptionsCapability {
	m.mu.RLock()
	if !force && m.optionProbe!=nil && time.Since(m.optionChecked)<6*time.Hour {
		cached:=*m.optionProbe
		m.mu.RUnlock()
		return cached
	}
	m.mu.RUnlock()
	result := OptionsCapability{Provider: "Alpha Vantage REALTIME_OPTIONS"}
	contracts, err := m.FetchOptions(ctx, "AAPL")
	if err != nil {
		result.Reason = err.Error()
	} else {
		result.Available = true
		result.Contracts = len(contracts)
	}
	result.CheckedAt=time.Now().UTC().Format(time.RFC3339)
	m.mu.Lock()
	m.optionProbe=&result
	m.optionChecked=time.Now()
	m.mu.Unlock()
	return result
}
