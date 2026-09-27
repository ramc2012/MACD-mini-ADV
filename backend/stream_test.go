package main

import (
	"context"
	"encoding/json"
	"fmt"
	"io"
	"net/http"
	"net/http/httptest"
	"strings"
	"sync"
	"testing"
	"time"

	"github.com/coder/websocket"
)

// fakeEngine speaks the engine's stream protocol: a snapshot on connect and
// on {"command":"snapshot"}, then sequenced events broadcast to every socket.
type fakeEngine struct {
	t      *testing.T
	mu     sync.Mutex
	seq    int64
	conns  map[*websocket.Conn]bool
	orders chan string
	server *httptest.Server
	ready  chan struct{}
}

func newFakeEngine(t *testing.T) *fakeEngine {
	e := &fakeEngine{t: t, conns: map[*websocket.Conn]bool{}, orders: make(chan string, 4), ready: make(chan struct{}, 16)}
	e.server = httptest.NewServer(http.HandlerFunc(e.serve))
	t.Cleanup(e.server.Close)
	return e
}

func (e *fakeEngine) snapshotFrame() []byte {
	return []byte(fmt.Sprintf(`{"seq":%d,"type":"snapshot","data":{"cut":%d}}`, e.seq, e.seq))
}

func (e *fakeEngine) serve(w http.ResponseWriter, r *http.Request) {
	if r.URL.Path == "/api/orders" {
		body, _ := io.ReadAll(r.Body)
		e.orders <- string(body)
		w.Header().Set("Content-Type", "application/json")
		w.WriteHeader(http.StatusBadRequest)
		_, _ = w.Write([]byte(`{"detail":"New trades are capped at 4 lots"}`))
		return
	}
	conn, err := websocket.Accept(w, r, &websocket.AcceptOptions{InsecureSkipVerify: true})
	if err != nil {
		return
	}
	conn.SetReadLimit(1 << 20)
	e.mu.Lock()
	_ = conn.Write(context.Background(), websocket.MessageText, e.snapshotFrame())
	e.conns[conn] = true
	e.mu.Unlock()
	e.ready <- struct{}{}
	for {
		_, message, err := conn.Read(context.Background())
		if err != nil {
			e.mu.Lock()
			delete(e.conns, conn)
			e.mu.Unlock()
			return
		}
		if strings.Contains(string(message), `"snapshot"`) {
			e.mu.Lock()
			_ = conn.Write(context.Background(), websocket.MessageText, e.snapshotFrame())
			e.mu.Unlock()
		}
	}
}

// emit broadcasts one sequenced event; skip>0 burns sequence numbers first,
// which is what an engine that dropped events for a slow reader looks like.
func (e *fakeEngine) emit(typ, data string, skip int64) {
	e.mu.Lock()
	defer e.mu.Unlock()
	e.seq += 1 + skip
	frame := []byte(fmt.Sprintf(`{"seq":%d,"type":%q,"data":%s}`, e.seq, typ, data))
	for conn := range e.conns {
		_ = conn.Write(context.Background(), websocket.MessageText, frame)
	}
}

type capturedBus struct {
	mu       sync.Mutex
	subjects map[string]int
	last     map[string][]byte
}

func (b *capturedBus) Publish(subject string, data []byte) error {
	b.mu.Lock()
	defer b.mu.Unlock()
	b.subjects[subject]++
	b.last[subject] = data
	return nil
}

func startFanout(t *testing.T, engine *fakeEngine, token string, bus publisher) (*streamHub, *httptest.Server) {
	t.Helper()
	g, err := newGateway(engine.server.URL, engine.server.URL)
	if err != nil {
		t.Fatal(err)
	}
	g.stream = newStreamHub(g.engine, token, []string{"https://desk.example.ts.net"}, bus)
	ctx, cancel := context.WithCancel(context.Background())
	t.Cleanup(cancel)
	go g.stream.run(ctx)
	select {
	case <-engine.ready:
	case <-time.After(5 * time.Second):
		t.Fatal("gateway never connected upstream")
	}
	waitFor(t, g.stream.connected)
	server := httptest.NewServer(g)
	t.Cleanup(server.Close)
	return g.stream, server
}

func waitFor(t *testing.T, cond func() bool) {
	t.Helper()
	deadline := time.Now().Add(5 * time.Second)
	for !cond() {
		if time.Now().After(deadline) {
			t.Fatal("condition not reached")
		}
		time.Sleep(5 * time.Millisecond)
	}
}

type received struct {
	Seq  int64           `json:"seq"`
	Type string          `json:"type"`
	Data json.RawMessage `json:"data"`
}

func dialBrowser(t *testing.T, server *httptest.Server, path string, header http.Header) *websocket.Conn {
	t.Helper()
	conn, _, err := websocket.Dial(context.Background(), "ws"+strings.TrimPrefix(server.URL, "http")+path,
		&websocket.DialOptions{HTTPHeader: header})
	if err != nil {
		t.Fatal(err)
	}
	conn.SetReadLimit(8 << 20)
	t.Cleanup(func() { conn.CloseNow() })
	return conn
}

func readFrame(t *testing.T, conn *websocket.Conn) received {
	t.Helper()
	ctx, cancel := context.WithTimeout(context.Background(), 5*time.Second)
	defer cancel()
	_, message, err := conn.Read(ctx)
	if err != nil {
		t.Fatal(err)
	}
	var frame received
	if err := json.Unmarshal(message, &frame); err != nil {
		t.Fatalf("bad frame %s: %v", message, err)
	}
	return frame
}

// readUntil reads frames until one of the given type, checking that every
// sequenced frame follows the previous one without a gap.
func readUntil(t *testing.T, conn *websocket.Conn, last *int64, stop string) []received {
	t.Helper()
	var frames []received
	for {
		frame := readFrame(t, conn)
		if frame.Seq != 0 {
			if *last != 0 && frame.Seq != *last+1 {
				t.Fatalf("sequence gap: %d after %d (%s)", frame.Seq, *last, frame.Type)
			}
			*last = frame.Seq
		}
		frames = append(frames, frame)
		if frame.Type == stop {
			return frames
		}
	}
}

func tick(symbol string, price float64) string {
	return fmt.Sprintf(`{"symbol":%q,"ltp":%g,"volume":%d,"timestamp":"2026-09-28T04:00:00.250000+00:00"}`, symbol, price, int(price*10))
}

func TestFanoutCoalescesTicksButDeliversEveryOrderInOrder(t *testing.T) {
	engine := newFakeEngine(t)
	bus := &capturedBus{subjects: map[string]int{}, last: map[string][]byte{}}
	hub, server := startFanout(t, engine, "", bus)
	browser := dialBrowser(t, server, "/ws/stream", nil)
	var last int64
	if first := readFrame(t, browser); first.Type != "snapshot" {
		t.Fatalf("first frame %s, want snapshot", first.Type)
	} else {
		last = first.Seq
	}

	const symbols, rounds = 30, 100
	for round := 0; round < rounds; round++ {
		for s := 0; s < symbols; s++ {
			engine.emit("tick", tick(fmt.Sprintf("NSE:S%d", s), float64(round+1)), 0)
		}
		engine.emit("portfolio", fmt.Sprintf(`{"equity":%d}`, round), 0)
		if round%10 == 0 {
			engine.emit("order", fmt.Sprintf(`{"order_id":"o%d"}`, round), 0)
		}
	}
	engine.emit("broker", `{"status":"connected"}`, 0)

	frames := readUntil(t, browser, &last, "broker")
	lastPrice := map[string]float64{}
	var orders []string
	for _, frame := range frames {
		switch frame.Type {
		case "tick":
			var row struct {
				Symbol string  `json:"symbol"`
				LTP    float64 `json:"ltp"`
			}
			_ = json.Unmarshal(frame.Data, &row)
			if row.LTP < lastPrice[row.Symbol] {
				t.Fatalf("%s went back from %g to %g", row.Symbol, lastPrice[row.Symbol], row.LTP)
			}
			lastPrice[row.Symbol] = row.LTP
		case "order":
			var row struct {
				OrderID string `json:"order_id"`
			}
			_ = json.Unmarshal(frame.Data, &row)
			orders = append(orders, row.OrderID)
		}
	}
	for s := 0; s < symbols; s++ {
		if got := lastPrice[fmt.Sprintf("NSE:S%d", s)]; got != rounds {
			t.Fatalf("NSE:S%d ended at %g, want the newest print %d", s, got, rounds)
		}
	}
	if strings.Join(orders, ",") != "o0,o10,o20,o30,o40,o50,o60,o70,o80,o90" {
		t.Fatalf("orders lost or reordered: %v", orders)
	}
	published := symbols*rounds + rounds + 11
	if len(frames) >= published {
		t.Fatalf("nothing was coalesced: %d frames for %d events", len(frames), published)
	}
	waitFor(t, func() bool { bus.mu.Lock(); defer bus.mu.Unlock(); return bus.subjects["md.tick.NSE:S0"] == rounds })
	var normalized busTick
	_ = json.Unmarshal(bus.last["md.tick.NSE:S29"], &normalized)
	if normalized.Version != 1 || normalized.LTP != rounds || normalized.ExchangeTsMs != 1790568000250 || normalized.Seq == 0 {
		t.Fatalf("bad bus tick: %+v", normalized)
	}
	if stats := hub.stats(); stats["bus"].(map[string]any)["published"].(int64) != symbols*rounds {
		t.Fatalf("bus stats: %+v", stats["bus"])
	}
}

func TestLateBrowserGetsSnapshotThenReplayOfWhatItMissed(t *testing.T) {
	engine := newFakeEngine(t)
	hub, server := startFanout(t, engine, "", nil)
	for i := 1; i <= 50; i++ {
		engine.emit("tick", tick("NSE:A", float64(i)), 0)
		engine.emit("order", fmt.Sprintf(`{"order_id":"o%d"}`, i), 0)
	}
	waitFor(t, func() bool { hub.mu.Lock(); defer hub.mu.Unlock(); return hub.lastSeq == 100 })
	engine.emit("broker", `{"status":"connected"}`, 0)

	browser := dialBrowser(t, server, "/ws/stream", nil)
	var last int64
	frames := readUntil(t, browser, &last, "broker")
	if frames[0].Type != "snapshot" || string(frames[0].Data) != `{"cut":0}` {
		t.Fatalf("expected the cached snapshot first, got %s %s", frames[0].Type, frames[0].Data)
	}
	orders, ticks := 0, 0
	for _, frame := range frames[1:] {
		switch frame.Type {
		case "order":
			orders++
		case "tick":
			ticks++
			if !strings.Contains(string(frame.Data), `"ltp":50`) {
				t.Fatalf("replayed a superseded tick: %s", frame.Data)
			}
		}
	}
	if orders != 50 || ticks != 1 {
		t.Fatalf("replay delivered %d orders and %d ticks, want 50 and 1", orders, ticks)
	}
}

func TestUpstreamGapResynchronisesEveryBrowser(t *testing.T) {
	engine := newFakeEngine(t)
	hub, server := startFanout(t, engine, "", nil)
	browser := dialBrowser(t, server, "/ws/stream", nil)
	var last int64
	readUntil(t, browser, &last, "snapshot")

	engine.emit("order", `{"order_id":"before"}`, 0)
	engine.emit("order", `{"order_id":"lost-for-us"}`, 5) // seq jumps: events were dropped
	frames := readUntil(t, browser, &last, "snapshot")
	if !strings.Contains(string(frames[len(frames)-1].Data), `"cut":7`) {
		t.Fatalf("resync snapshot should be a fresh cut: %s", frames[len(frames)-1].Data)
	}
	if hub.upstreamGaps.Load() != 1 {
		t.Fatalf("gap not counted")
	}
	engine.emit("order", `{"order_id":"after"}`, 0)
	frames = readUntil(t, browser, &last, "order")
	if !strings.Contains(string(frames[len(frames)-1].Data), "after") {
		t.Fatalf("live events did not resume after the resync")
	}
}

func TestBrowserResyncCommandAndPingAreAnswered(t *testing.T) {
	engine := newFakeEngine(t)
	_, server := startFanout(t, engine, "", nil)
	browser := dialBrowser(t, server, "/ws/stream", nil)
	var last int64
	readUntil(t, browser, &last, "snapshot")
	_ = browser.Write(context.Background(), websocket.MessageText, []byte(`{"command":"ping"}`))
	if frame := readFrame(t, browser); frame.Type != "pong" || frame.Seq != 0 {
		t.Fatalf("pong must be unsequenced: %+v", frame)
	}
	_ = browser.Write(context.Background(), websocket.MessageText, []byte(`{"command":"snapshot"}`))
	readUntil(t, browser, &last, "snapshot")
}

func TestOrderCommandIsForwardedAndRefusalReported(t *testing.T) {
	engine := newFakeEngine(t)
	_, server := startFanout(t, engine, "", nil)
	browser := dialBrowser(t, server, "/ws/stream", nil)
	var last int64
	readUntil(t, browser, &last, "snapshot")
	_ = browser.Write(context.Background(), websocket.MessageText,
		[]byte(`{"command":"order","data":{"symbol":"NSE:X","side":"BUY","lots":9}}`))
	select {
	case body := <-engine.orders:
		if body != `{"symbol":"NSE:X","side":"BUY","lots":9}` {
			t.Fatalf("order body changed in transit: %s", body)
		}
	case <-time.After(5 * time.Second):
		t.Fatal("order never reached the engine")
	}
	frame := readFrame(t, browser)
	if frame.Type != "order_error" || !strings.Contains(string(frame.Data), "capped at 4 lots") {
		t.Fatalf("refusal not reported: %+v %s", frame, frame.Data)
	}
}

func TestForeignOriginAndWrongTokenAreRefused(t *testing.T) {
	engine := newFakeEngine(t)
	_, server := startFanout(t, engine, "secret", nil)
	url := "ws" + strings.TrimPrefix(server.URL, "http") + "/ws/stream"
	for _, tc := range []struct {
		path, origin string
		status       int
	}{
		{"?token=secret", "https://evil.example", http.StatusForbidden},
		{"?token=secret", "http://rebound.attacker.test:3200", http.StatusForbidden},
		{"?token=wrong", "http://localhost:3200", http.StatusUnauthorized},
	} {
		_, resp, err := websocket.Dial(context.Background(), url+tc.path,
			&websocket.DialOptions{HTTPHeader: http.Header{"Origin": {tc.origin}}})
		if err == nil || resp == nil || resp.StatusCode != tc.status {
			t.Fatalf("%s %s: got %v, want HTTP %d", tc.origin, tc.path, resp, tc.status)
		}
	}
	for _, origin := range []string{"http://localhost:3200", "http://127.0.0.1:3100", "https://desk.example.ts.net"} {
		conn := dialBrowser(t, server, "/ws/stream?token=secret", http.Header{"Origin": {origin}})
		if frame := readFrame(t, conn); frame.Type != "snapshot" {
			t.Fatalf("%s refused", origin)
		}
	}
}

func TestSlowBrowserIsResyncedRatherThanGrowingWithoutBound(t *testing.T) {
	client := newStreamClient(nil)
	client.reset(&cachedSnapshot{seq: 0, data: json.RawMessage(`{}`)}, nil)
	for i := int64(1); i <= clientMaxMandatory+1; i++ {
		client.enqueue(streamEvent{seq: i, typ: "order", data: json.RawMessage(`{}`)})
	}
	if messages, resync := client.take(); !resync || messages != nil {
		t.Fatal("an overflowing browser must be resynchronised")
	}
}

func TestQueueNumbersOnlySequencedFramesAndKeepsBarsApart(t *testing.T) {
	client := newStreamClient(nil)
	client.reset(&cachedSnapshot{seq: 10, data: json.RawMessage(`{"s":1}`)}, nil)
	client.enqueue(streamEvent{seq: 9, typ: "order", data: json.RawMessage(`{"stale":true}`)}) // inside the snapshot
	for i, row := range []string{
		`{"symbol":"NSE:A","timestamp":60,"close":1}`,
		`{"symbol":"NSE:A","timestamp":60,"close":2}`,
		`{"symbol":"NSE:A","timestamp":120,"close":3}`,
	} {
		key, _ := classify("candle", json.RawMessage(row))
		client.enqueue(streamEvent{seq: int64(11 + i), typ: "candle", data: json.RawMessage(row), key: key})
	}
	client.push([]byte(`{"type":"pong","data":{}}`))
	messages, _ := client.take()
	got := make([]string, len(messages))
	for i, message := range messages {
		got[i] = string(message)
	}
	want := []string{
		`{"seq":1,"type":"snapshot","data":{"s":1}}`,
		`{"seq":2,"type":"candle","data":{"symbol":"NSE:A","timestamp":60,"close":2}}`,
		`{"seq":3,"type":"candle","data":{"symbol":"NSE:A","timestamp":120,"close":3}}`,
		`{"type":"pong","data":{}}`,
	}
	if strings.Join(got, "\n") != strings.Join(want, "\n") {
		t.Fatalf("got\n%s\nwant\n%s", strings.Join(got, "\n"), strings.Join(want, "\n"))
	}
}

func TestTickSubjectsAreSafeNatsTokens(t *testing.T) {
	if got := tickSubject("NSE:M&M-EQ"); got != "md.tick.NSE:M&M-EQ" {
		t.Fatal(got)
	}
	if got := tickSubject("BSE:A.B*C>D E"); got != "md.tick.BSE:A_B_C_D_E" {
		t.Fatal(got)
	}
	if _, err := normalizeTick(1, &engineTick{Symbol: "X", Timestamp: "not a time"}, time.Now()); err == nil {
		t.Fatal("unparseable exchange time must be refused, not stamped with receipt time")
	}
}

func TestAcknowledgingBrowserIsHeldToTheWindowAndStaysCurrent(t *testing.T) {
	client := newStreamClient(nil)
	client.reset(&cachedSnapshot{seq: 0, data: json.RawMessage(`{}`)}, nil)
	client.acknowledge(0) // opts in to flow control
	messages, _ := client.take()
	if len(messages) != 1 {
		t.Fatalf("snapshot should go out: %d", len(messages))
	}
	seq := int64(0)
	for round := 0; round < 3; round++ { // three rounds of 1,500 symbols
		for s := 0; s < 1500; s++ {
			seq++
			data := json.RawMessage(fmt.Sprintf(`{"symbol":"NSE:S%d","ltp":%d}`, s, round))
			key, _ := classify("tick", data)
			client.enqueue(streamEvent{seq: seq, typ: "tick", data: data, key: key})
		}
	}
	messages, _ = client.take()
	if len(messages) != clientAckWindow-1 {
		t.Fatalf("sent %d frames with 1 unacknowledged, want %d", len(messages), clientAckWindow-1)
	}
	if more, _ := client.take(); len(more) != 0 {
		t.Fatalf("window full, yet %d more frames went out", len(more))
	}
	client.push([]byte(`{"type":"pong","data":{}}`))
	if more, _ := client.take(); len(more) != 1 {
		t.Fatalf("unsequenced replies must not wait for the window")
	}
	// Held symbols coalesced to their newest print while the window was full.
	client.acknowledge(clientAckWindow)
	messages, _ = client.take()
	if len(messages) != 1500-(clientAckWindow-1) {
		t.Fatalf("after the ack, %d frames; want the %d held symbols once each", len(messages), 1500-(clientAckWindow-1))
	}
	for _, message := range messages {
		if !strings.Contains(string(message), `"ltp":2}`) {
			t.Fatalf("a held symbol was sent stale: %s", message)
		}
	}
}

func TestBrowserThatNeverAcknowledgesIsNotThrottled(t *testing.T) {
	client := newStreamClient(nil)
	client.reset(&cachedSnapshot{seq: 0, data: json.RawMessage(`{}`)}, nil)
	for i := int64(1); i <= 5000; i++ {
		client.enqueue(streamEvent{seq: i, typ: "order", data: json.RawMessage(`{}`)})
	}
	if messages, _ := client.take(); len(messages) != 5001 {
		t.Fatalf("an unacknowledging client got %d of 5001 frames", len(messages))
	}
}
