package main

import (
	"context"
	"encoding/json"
	"errors"
	"fmt"
	"net/http"
	"net/url"
	"time"
)

type MinuteBar struct {
	Symbol string `json:"symbol"`
	At time.Time `json:"at"`
	Open float64 `json:"open"`
	High float64 `json:"high"`
	Low float64 `json:"low"`
	Close float64 `json:"close"`
	Volume float64 `json:"volume"`
	Source string `json:"source"`
}

func (m *Market) FetchMinuteCandles(ctx context.Context,symbol string,from,to time.Time) ([]MinuteBar,error) {
	key:=m.Keys().Finnhub
	if key=="" { return nil,errors.New("Finnhub key is not configured") }
	if !stockSet()[symbol] { return nil,errors.New("symbol is outside the 100-stock universe") }
	query:=url.Values{}
	query.Set("symbol",symbol)
	query.Set("resolution","1")
	query.Set("from",fmt.Sprint(from.Unix()))
	query.Set("to",fmt.Sprint(to.Unix()))
	request,err:=http.NewRequestWithContext(ctx,http.MethodGet,"https://finnhub.io/api/v1/stock/candle?"+query.Encode(),nil)
	if err!=nil { return nil,err }
	request.Header.Set("X-Finnhub-Token",key)
	response,err:=m.client.Do(request)
	if err!=nil { return nil,errors.New(redact(err.Error(),key)) }
	defer response.Body.Close()
	if response.StatusCode!=200 { return nil,fmt.Errorf("Finnhub minute candles HTTP %d",response.StatusCode) }
	var body struct { Status string `json:"s"`;Time []int64 `json:"t"`;Open []float64 `json:"o"`;High []float64 `json:"h"`;Low []float64 `json:"l"`;Close []float64 `json:"c"`;Volume []float64 `json:"v"` }
	if err=json.NewDecoder(response.Body).Decode(&body);err!=nil{return nil,err}
	if body.Status!="ok" {return nil,fmt.Errorf("Finnhub minute candles: %s",body.Status)}
	if len(body.Time)!=len(body.Close)||len(body.Time)!=len(body.Open)||len(body.Time)!=len(body.High)||len(body.Time)!=len(body.Low)||len(body.Time)!=len(body.Volume){return nil,errors.New("Finnhub minute candle arrays have inconsistent lengths")}
	bars:=make([]MinuteBar,0,len(body.Time))
	for i,stamp:=range body.Time {
		if body.Close[i]<=0 {continue}
		bars=append(bars,MinuteBar{symbol,time.Unix(stamp,0).UTC(),body.Open[i],body.High[i],body.Low[i],body.Close[i],body.Volume[i],"Finnhub minute candle"})
	}
	return bars,nil
}
