package main

// Stream fan-out: the gateway holds ONE websocket to the engine and serves
// every browser from it.
//
// The engine gives each subscriber a 512-event queue and drops the oldest
// event when it fills. A browser that stalls for a few hundred milliseconds at
// the open (a phone, a background tab) then sees a sequence gap, asks for a
// full snapshot, and the engine spends ~130 ms of its only event loop building
// 1.7 MB for it -- during which every other subscriber falls further behind.
//
// Here the engine has a single fast reader. Each browser gets its own queue in
// which state-bearing events coalesce (the newest tick per symbol replaces the
// older one, the newest forming bar per symbol and bar time replaces the
// older one, portfolio totals replace each other), while orders, trades,
// signals and broker events are always delivered, in order. Sequence numbers
// are rewritten per browser, so coalescing never looks like a gap and the
// terminal never needs to resynchronise because it was slow.

import (
	"bytes"
	"context"
	"crypto/subtle"
	"encoding/json"
	"errors"
	"fmt"
	"io"
	"log"
	"net/http"
	"net/url"
	"strconv"
	"strings"
	"sync"
	"sync/atomic"
	"time"

	"github.com/coder/websocket"
)

const (
	// Upstream events kept for replay to a browser that joins after the
	// cached snapshot was cut: ~10-20 s at the open's busiest rate.
	ringCapacity = 50_000
	// Deliver-always events a browser may have queued before it is judged
	// unable to keep up and is sent a fresh snapshot instead.
	clientMaxMandatory = 20_000
	// Coalescing window: the terminal renders quotes at 10 Hz, so waiting
	// 50 ms lets bursts for one symbol collapse into its newest print.
	clientFlushDelay   = 50 * time.Millisecond
	clientWriteTimeout = 15 * time.Second
	// Frames a browser may have unacknowledged once it acknowledges at all.
	// Proxies between here and the browser (nginx, Docker's port forwarder)
	// buffer megabytes, so TCP backpressure reaches this process far too late
	// to keep a slow browser current. Holding frames here instead lets newer
	// ticks replace older ones, bounding how stale a slow browser can be.
	clientAckWindow   = 1_000
	clientReadLimit   = 64 << 10
	upstreamReadLimit = 256 << 20
)

type upstreamFrame struct {
	Seq  int64           `json:"seq"`
	Type string          `json:"type"`
	Data json.RawMessage `json:"data"`
}

type streamEvent struct {
	seq  int64
	typ  string
	data json.RawMessage
	key  string // coalescing key; empty means the event must be delivered
}

type cachedSnapshot struct {
	seq  int64
	data json.RawMessage
}

// publisher is the tick bus. nil disables publishing.
type publisher interface {
	Publish(subject string, data []byte) error
}

type streamHub struct {
	streamURL   string
	ordersURL   string
	token       string
	origins     map[string]bool
	orderClient *http.Client
	bus         publisher

	mu       sync.Mutex
	clients  map[*streamClient]struct{}
	ring     []streamEvent
	ringHead int
	ringLen  int
	lastSeq  int64
	snap     *cachedSnapshot
	upstream bool

	fetchMu sync.Mutex // one snapshot fetch at a time

	upstreamEvents   atomic.Int64
	upstreamGaps     atomic.Int64
	upstreamConnects atomic.Int64
	snapshotsFetched atomic.Int64
	busPublished     atomic.Int64
	busErrors        atomic.Int64
	clientsServed    atomic.Int64
	lastError        atomic.Value // string
}

func newStreamHub(engine *url.URL, token string, origins []string, bus publisher) *streamHub {
	stream := *engine
	stream.Scheme = map[string]string{"http": "ws", "https": "wss"}[engine.Scheme]
	stream.Path = "/ws/stream"
	if token != "" {
		stream.RawQuery = url.Values{"token": {token}}.Encode()
	}
	orders := *engine
	orders.Path = "/api/orders"
	allowed := map[string]bool{}
	for _, origin := range origins {
		if origin = strings.TrimRight(strings.TrimSpace(origin), "/"); origin != "" {
			allowed[strings.ToLower(origin)] = true
		}
	}
	return &streamHub{
		streamURL:   stream.String(),
		ordersURL:   orders.String(),
		token:       token,
		origins:     allowed,
		orderClient: &http.Client{Timeout: 30 * time.Second},
		bus:         bus,
		clients:     map[*streamClient]struct{}{},
		ring:        make([]streamEvent, ringCapacity),
	}
}

// ---- ring of recent upstream events ----------------------------------------

func (h *streamHub) ringPush(ev streamEvent) {
	index := (h.ringHead + h.ringLen) % len(h.ring)
	h.ring[index] = ev
	if h.ringLen < len(h.ring) {
		h.ringLen++
	} else {
		h.ringHead = (h.ringHead + 1) % len(h.ring)
	}
}

func (h *streamHub) ringReset() {
	h.ringHead, h.ringLen = 0, 0
}

// ringSince returns the buffered events after seq, oldest first, and whether
// the buffer still reaches back that far.
func (h *streamHub) ringSince(seq int64) ([]streamEvent, bool) {
	if h.lastSeq <= seq {
		return nil, true
	}
	if h.ringLen == 0 || h.ring[h.ringHead].seq > seq+1 {
		return nil, false
	}
	out := make([]streamEvent, 0, h.lastSeq-seq)
	for i := 0; i < h.ringLen; i++ {
		ev := h.ring[(h.ringHead+i)%len(h.ring)]
		if ev.seq > seq {
			out = append(out, ev)
		}
	}
	return out, true
}

// ---- upstream -----------------------------------------------------------------

func (h *streamHub) run(ctx context.Context) {
	backoff := time.Second
	for ctx.Err() == nil {
		started := time.Now()
		err := h.consume(ctx)
		h.mu.Lock()
		h.upstream = false
		h.mu.Unlock()
		if ctx.Err() != nil {
			return
		}
		h.lastError.Store(err.Error())
		log.Printf("engine stream: %v", err)
		if time.Since(started) > time.Minute {
			backoff = time.Second
		}
		select {
		case <-ctx.Done():
			return
		case <-time.After(backoff):
		}
		backoff = min(backoff*2, 15*time.Second)
	}
}

func (h *streamHub) consume(ctx context.Context) error {
	conn, _, err := websocket.Dial(ctx, h.streamURL, nil)
	if err != nil {
		return fmt.Errorf("dial: %w", err)
	}
	defer conn.CloseNow()
	conn.SetReadLimit(upstreamReadLimit)
	h.upstreamConnects.Add(1)

	awaitingSnapshot := true // the engine always opens with one
	for {
		_, message, err := conn.Read(ctx)
		if err != nil {
			return fmt.Errorf("read: %w", err)
		}
		var frame upstreamFrame
		if err := json.Unmarshal(message, &frame); err != nil || frame.Type == "" {
			continue
		}
		if frame.Type == "snapshot" {
			h.installSnapshot(frame)
			awaitingSnapshot = false
			continue
		}
		if frame.Seq == 0 || awaitingSnapshot {
			continue // unsequenced replies, or pre-resync leftovers
		}
		h.mu.Lock()
		gap := h.lastSeq != 0 && frame.Seq != h.lastSeq+1
		stale := frame.Seq <= h.lastSeq
		h.mu.Unlock()
		if stale {
			continue
		}
		if gap {
			// The engine dropped events for us. Everything derived from the
			// ring is now suspect: take a fresh cut on this same connection
			// and resynchronise every browser from it.
			h.upstreamGaps.Add(1)
			awaitingSnapshot = true
			if err := conn.Write(ctx, websocket.MessageText, []byte(`{"command":"snapshot"}`)); err != nil {
				return fmt.Errorf("request snapshot: %w", err)
			}
			continue
		}
		ev := streamEvent{seq: frame.Seq, typ: frame.Type, data: frame.Data}
		var tick *engineTick
		ev.key, tick = classify(frame.Type, frame.Data)
		h.apply(ev)
		if tick != nil && h.bus != nil {
			h.publishTick(frame.Seq, tick)
		}
	}
}

func (h *streamHub) installSnapshot(frame upstreamFrame) {
	h.mu.Lock()
	defer h.mu.Unlock()
	h.snap = &cachedSnapshot{seq: frame.Seq, data: frame.Data}
	h.lastSeq = frame.Seq
	h.ringReset()
	h.upstream = true
	for client := range h.clients {
		client.reset(h.snap, nil)
	}
}

func (h *streamHub) apply(ev streamEvent) {
	h.upstreamEvents.Add(1)
	h.mu.Lock()
	defer h.mu.Unlock()
	h.lastSeq = ev.seq
	h.ringPush(ev)
	for client := range h.clients {
		client.enqueue(ev)
	}
}

// fetchSnapshot takes a fresh cut on a short-lived second connection, so the
// main connection's queue -- which other browsers depend on -- is untouched.
func (h *streamHub) fetchSnapshot(ctx context.Context) (*cachedSnapshot, error) {
	ctx, cancel := context.WithTimeout(ctx, 30*time.Second)
	defer cancel()
	conn, _, err := websocket.Dial(ctx, h.streamURL, nil)
	if err != nil {
		return nil, err
	}
	defer conn.CloseNow()
	conn.SetReadLimit(upstreamReadLimit)
	for {
		_, message, err := conn.Read(ctx)
		if err != nil {
			return nil, err
		}
		var frame upstreamFrame
		if json.Unmarshal(message, &frame) == nil && frame.Type == "snapshot" {
			h.snapshotsFetched.Add(1)
			_ = conn.Close(websocket.StatusNormalClosure, "")
			return &cachedSnapshot{seq: frame.Seq, data: frame.Data}, nil
		}
	}
}

// attach (re)initialises a browser from the cached snapshot plus the events
// after it, taking a fresh snapshot only when the ring no longer reaches back.
func (h *streamHub) attach(ctx context.Context, client *streamClient) error {
	for attempt := 0; attempt < 3; attempt++ {
		h.mu.Lock()
		if h.snap != nil {
			if events, ok := h.ringSince(h.snap.seq); ok {
				client.reset(h.snap, events)
				h.clients[client] = struct{}{}
				h.mu.Unlock()
				return nil
			}
		}
		h.mu.Unlock()

		h.fetchMu.Lock()
		h.mu.Lock()
		fresh := h.snap != nil
		if fresh {
			_, fresh = h.ringSince(h.snap.seq)
		}
		h.mu.Unlock()
		if !fresh { // nobody refreshed it while we waited
			snap, err := h.fetchSnapshot(ctx)
			if err != nil {
				h.fetchMu.Unlock()
				return err
			}
			h.mu.Lock()
			// Keep whichever cut is newer. A cut ahead of the main connection
			// is fine: the browser skips live events it already contains.
			if h.snap == nil || snap.seq >= h.snap.seq {
				h.snap = snap
			}
			h.mu.Unlock()
		}
		h.fetchMu.Unlock()
	}
	return errors.New("engine snapshot kept moving out of the replay window")
}

func (h *streamHub) detach(client *streamClient) {
	h.mu.Lock()
	delete(h.clients, client)
	h.mu.Unlock()
}

// ---- browsers --------------------------------------------------------------

func (h *streamHub) originAllowed(origin string) bool {
	if origin == "" {
		return true // not a browser; the token governs it
	}
	if h.origins[strings.ToLower(strings.TrimRight(origin, "/"))] {
		return true
	}
	parsed, err := url.Parse(origin)
	if err != nil {
		return false
	}
	switch strings.ToLower(parsed.Hostname()) {
	case "localhost", "127.0.0.1", "::1":
		return true
	}
	return false
}

func (h *streamHub) serveClient(w http.ResponseWriter, r *http.Request) {
	if h.token != "" && subtle.ConstantTimeCompare([]byte(r.URL.Query().Get("token")), []byte(h.token)) != 1 {
		writeError(w, http.StatusUnauthorized, "invalid_token", "Invalid API token")
		return
	}
	// CORS does not cover websockets, and this socket accepts orders.
	if !h.originAllowed(r.Header.Get("Origin")) {
		writeError(w, http.StatusForbidden, "origin_refused", "This origin may not open the stream")
		return
	}
	conn, err := websocket.Accept(w, r, &websocket.AcceptOptions{
		InsecureSkipVerify: true, // origin checked above, with loopback rules
		CompressionMode:    websocket.CompressionContextTakeover,
	})
	if err != nil {
		return
	}
	conn.SetReadLimit(clientReadLimit)
	ctx, cancel := context.WithCancel(r.Context())
	defer cancel()
	client := newStreamClient(conn)
	if err := h.attach(ctx, client); err != nil {
		_ = conn.Close(websocket.StatusTryAgainLater, "engine stream unavailable")
		return
	}
	h.clientsServed.Add(1)
	defer h.detach(client)
	go h.readCommands(ctx, cancel, client)
	h.writeLoop(ctx, client)
	_ = conn.Close(websocket.StatusNormalClosure, "")
}

func (h *streamHub) readCommands(ctx context.Context, cancel context.CancelFunc, client *streamClient) {
	defer cancel()
	for {
		_, message, err := client.conn.Read(ctx)
		if err != nil {
			return
		}
		var command struct {
			Command string          `json:"command"`
			Data    json.RawMessage `json:"data"`
		}
		if json.Unmarshal(message, &command) != nil {
			continue
		}
		switch command.Command {
		case "snapshot":
			client.requestResync()
		case "ping":
			client.push([]byte(`{"type":"pong","data":{}}`))
		case "ack":
			var ack struct {
				Seq int64 `json:"seq"`
			}
			if json.Unmarshal(message, &ack) == nil {
				client.acknowledge(ack.Seq)
			}
		case "order":
			go h.forwardOrder(ctx, client, command.Data)
		}
	}
}

func (h *streamHub) forwardOrder(ctx context.Context, client *streamClient, data json.RawMessage) {
	fail := func(message string) {
		body, _ := json.Marshal(map[string]any{"type": "order_error", "data": map[string]string{"message": message}})
		client.push(body)
	}
	req, err := http.NewRequestWithContext(ctx, http.MethodPost, h.ordersURL, bytes.NewReader(data))
	if err != nil {
		fail("could not form order request")
		return
	}
	req.Header.Set("Content-Type", "application/json")
	if h.token != "" {
		req.Header.Set("X-Macd-Token", h.token)
	}
	resp, err := h.orderClient.Do(req)
	if err != nil {
		fail("trading engine unavailable")
		return
	}
	defer resp.Body.Close()
	body, _ := io.ReadAll(io.LimitReader(resp.Body, 4096))
	if resp.StatusCode >= 300 {
		var detail struct {
			Detail any `json:"detail"`
		}
		if json.Unmarshal(body, &detail) == nil && detail.Detail != nil {
			fail(fmt.Sprint(detail.Detail))
		} else {
			fail(fmt.Sprintf("order refused (HTTP %d)", resp.StatusCode))
		}
	}
	// A filled order reaches every browser as the engine's own order event.
}

func (h *streamHub) writeLoop(ctx context.Context, client *streamClient) {
	for {
		select {
		case <-ctx.Done():
			return
		case <-client.notify:
		}
		select {
		case <-ctx.Done():
			return
		case <-time.After(clientFlushDelay):
		}
		messages, resync := client.take()
		if resync {
			if err := h.attach(ctx, client); err != nil {
				return
			}
			client.resyncs.Add(1)
			continue
		}
		for _, message := range messages {
			writeCtx, cancel := context.WithTimeout(ctx, clientWriteTimeout)
			err := client.conn.Write(writeCtx, websocket.MessageText, message)
			cancel()
			if err != nil {
				return
			}
			client.sent.Add(1)
		}
	}
}

// ---- per-browser queue -------------------------------------------------------

type queued struct {
	key  string
	typ  string
	data json.RawMessage
	raw  []byte // unsequenced reply to this browser only
}

type streamClient struct {
	conn   *websocket.Conn
	notify chan struct{}

	mu        sync.Mutex
	queue     []*queued
	index     map[string]*queued
	mandatory int
	skipUntil int64
	nextSeq   int64
	resync    bool
	// Flow control starts with the browser's first acknowledgement; a client
	// that never acknowledges is written to as fast as the socket accepts.
	flowControl bool
	acked       int64

	sent      atomic.Int64
	coalesced atomic.Int64
	resyncs   atomic.Int64
	held      atomic.Int64 // flushes deferred by the acknowledgement window
}

func (c *streamClient) acknowledge(seq int64) {
	c.mu.Lock()
	c.flowControl = true
	if seq > c.acked && seq <= c.nextSeq {
		c.acked = seq
	}
	c.mu.Unlock()
	c.signal()
}

func newStreamClient(conn *websocket.Conn) *streamClient {
	return &streamClient{conn: conn, notify: make(chan struct{}, 1), index: map[string]*queued{}}
}

func (c *streamClient) signal() {
	select {
	case c.notify <- struct{}{}:
	default:
	}
}

// reset replaces everything pending with a snapshot and the events after it.
// Called with the hub lock held, so no live event can slip between the two.
func (c *streamClient) reset(snap *cachedSnapshot, events []streamEvent) {
	c.mu.Lock()
	c.queue = []*queued{{typ: "snapshot", data: snap.data}}
	c.index = map[string]*queued{}
	c.mandatory = 0
	c.skipUntil = snap.seq
	c.resync = false
	for _, ev := range events {
		c.enqueueLocked(ev)
	}
	c.mu.Unlock()
	c.signal()
}

func (c *streamClient) enqueue(ev streamEvent) {
	c.mu.Lock()
	c.enqueueLocked(ev)
	c.mu.Unlock()
	c.signal()
}

func (c *streamClient) enqueueLocked(ev streamEvent) {
	if ev.seq <= c.skipUntil || c.resync {
		return
	}
	if ev.key != "" {
		if slot, ok := c.index[ev.key]; ok {
			slot.data = ev.data // newest state, at the position of the first
			c.coalesced.Add(1)
			return
		}
		slot := &queued{key: ev.key, typ: ev.typ, data: ev.data}
		c.queue = append(c.queue, slot)
		c.index[ev.key] = slot
		return
	}
	c.queue = append(c.queue, &queued{typ: ev.typ, data: ev.data})
	c.mandatory++
	if c.mandatory > clientMaxMandatory {
		// Too far behind for replay to be cheaper than a fresh cut.
		c.queue, c.index, c.mandatory, c.resync = nil, map[string]*queued{}, 0, true
	}
}

func (c *streamClient) push(raw []byte) {
	c.mu.Lock()
	c.queue = append(c.queue, &queued{raw: raw})
	c.mu.Unlock()
	c.signal()
}

func (c *streamClient) requestResync() {
	c.mu.Lock()
	c.queue, c.index, c.mandatory, c.resync = nil, map[string]*queued{}, 0, true
	c.mu.Unlock()
	c.signal()
}

// take drains what the acknowledgement window allows, numbering it
// contiguously for this browser. What stays queued keeps coalescing.
func (c *streamClient) take() ([][]byte, bool) {
	c.mu.Lock()
	if c.resync {
		c.mu.Unlock()
		return nil, true
	}
	budget := len(c.queue)
	if c.flowControl {
		budget = clientAckWindow - int(c.nextSeq-c.acked)
	}
	// Replies to this browser alone (pong, order_error) carry no sequence
	// number and never wait for the window.
	var queue, kept []*queued
	for _, slot := range c.queue {
		switch {
		case slot.raw != nil:
			queue = append(queue, slot)
		case budget > 0:
			queue = append(queue, slot)
			budget--
		default:
			kept = append(kept, slot)
		}
	}
	c.queue = kept
	if len(c.queue) > 0 {
		c.held.Add(1)
	}
	c.mandatory = 0
	for _, slot := range queue {
		if slot.key != "" {
			delete(c.index, slot.key)
		}
	}
	for _, slot := range c.queue {
		if slot.key == "" && slot.raw == nil {
			c.mandatory++
		}
	}
	first := c.nextSeq
	for _, slot := range queue {
		if slot.raw == nil {
			c.nextSeq++
		}
	}
	c.mu.Unlock()

	messages := make([][]byte, 0, len(queue))
	seq := first
	for _, slot := range queue {
		if slot.raw != nil {
			messages = append(messages, slot.raw)
			continue
		}
		seq++
		messages = append(messages, frame(seq, slot.typ, slot.data))
	}
	return messages, false
}

func frame(seq int64, typ string, data json.RawMessage) []byte {
	var b bytes.Buffer
	b.Grow(len(data) + len(typ) + 40)
	b.WriteString(`{"seq":`)
	b.WriteString(strconv.FormatInt(seq, 10))
	b.WriteString(`,"type":`)
	quoted, _ := json.Marshal(typ)
	b.Write(quoted)
	b.WriteString(`,"data":`)
	if len(data) == 0 {
		b.WriteString("null")
	} else {
		b.Write(data)
	}
	b.WriteByte('}')
	return b.Bytes()
}

// ---- classification and the tick bus ----------------------------------------

// engineTick is the engine's published Tick (events.json_value of the dataclass).
type engineTick struct {
	Symbol       string   `json:"symbol"`
	LTP          float64  `json:"ltp"`
	Volume       float64  `json:"volume"`
	Timestamp    string   `json:"timestamp"`
	Bid          *float64 `json:"bid"`
	Ask          *float64 `json:"ask"`
	BidQty       *float64 `json:"bid_qty"`
	AskQty       *float64 `json:"ask_qty"`
	LastQty      *float64 `json:"last_qty"`
	OpenInterest *float64 `json:"open_interest"`
}

func classify(typ string, data json.RawMessage) (string, *engineTick) {
	switch typ {
	case "portfolio", "mp_portfolio", "blast_portfolio":
		return typ, nil // full state each time
	case "tick":
		var tick engineTick
		if json.Unmarshal(data, &tick) == nil && tick.Symbol != "" {
			return "tick\x00" + tick.Symbol, &tick
		}
	case "candle", "indicator":
		// Keyed by bar time too: the last update of a bar that has just
		// closed is its only record, so a newer bar must not replace it.
		var row struct {
			Symbol    string          `json:"symbol"`
			Timestamp json.RawMessage `json:"timestamp"`
		}
		if json.Unmarshal(data, &row) == nil && row.Symbol != "" {
			return typ + "\x00" + row.Symbol + "\x00" + string(row.Timestamp), nil
		}
	}
	return "", nil
}

// busTick is the versioned market-data contract on the bus. It carries the
// exchange time and the gateway's receipt time separately, and the engine's
// sequence number so a consumer can detect loss.
type busTick struct {
	Version      int      `json:"v"`
	Seq          int64    `json:"seq"`
	Symbol       string   `json:"symbol"`
	LTP          float64  `json:"ltp"`
	Volume       int64    `json:"volume"`
	ExchangeTsMs int64    `json:"exchange_ts_ms"`
	GatewayTsMs  int64    `json:"gateway_ts_ms"`
	Bid          *float64 `json:"bid,omitempty"`
	Ask          *float64 `json:"ask,omitempty"`
	BidQty       *float64 `json:"bid_qty,omitempty"`
	AskQty       *float64 `json:"ask_qty,omitempty"`
	LastQty      *float64 `json:"last_qty,omitempty"`
	OpenInterest *float64 `json:"open_interest,omitempty"`
}

func tickSubject(symbol string) string {
	// NATS subject tokens may not contain separators or wildcards.
	return "md.tick." + strings.Map(func(r rune) rune {
		switch r {
		case '.', '*', '>', ' ', '\t', '\r', '\n':
			return '_'
		}
		return r
	}, symbol)
}

func normalizeTick(seq int64, tick *engineTick, received time.Time) ([]byte, error) {
	exchange, err := time.Parse(time.RFC3339Nano, tick.Timestamp)
	if err != nil {
		return nil, fmt.Errorf("tick timestamp %q: %w", tick.Timestamp, err)
	}
	return json.Marshal(busTick{
		Version: 1, Seq: seq, Symbol: tick.Symbol, LTP: tick.LTP, Volume: int64(tick.Volume),
		ExchangeTsMs: exchange.UnixMilli(), GatewayTsMs: received.UnixMilli(),
		Bid: tick.Bid, Ask: tick.Ask, BidQty: tick.BidQty, AskQty: tick.AskQty,
		LastQty: tick.LastQty, OpenInterest: tick.OpenInterest,
	})
}

func (h *streamHub) publishTick(seq int64, tick *engineTick) {
	payload, err := normalizeTick(seq, tick, time.Now())
	if err == nil {
		err = h.bus.Publish(tickSubject(tick.Symbol), payload)
	}
	if err != nil {
		h.busErrors.Add(1)
		h.lastError.Store("bus: " + err.Error())
		return
	}
	h.busPublished.Add(1)
}

// ---- stats ---------------------------------------------------------------------

type clientStats struct {
	Queued      int   `json:"queued"`
	Sent        int64 `json:"sent"`
	Coalesced   int64 `json:"coalesced"`
	Resyncs     int64 `json:"resyncs"`
	FlowControl bool  `json:"flow_control"`
	Unacked     int64 `json:"unacked"`
	Held        int64 `json:"held"`
}

func (h *streamHub) stats() map[string]any {
	h.mu.Lock()
	clients := make([]clientStats, 0, len(h.clients))
	for client := range h.clients {
		client.mu.Lock()
		queuedCount, flow, unacked := len(client.queue), client.flowControl, client.nextSeq-client.acked
		client.mu.Unlock()
		clients = append(clients, clientStats{
			Queued: queuedCount, Sent: client.sent.Load(),
			Coalesced: client.coalesced.Load(), Resyncs: client.resyncs.Load(),
			FlowControl: flow, Unacked: unacked, Held: client.held.Load(),
		})
	}
	var snapshotSeq int64
	var snapshotBytes int
	if h.snap != nil {
		snapshotSeq, snapshotBytes = h.snap.seq, len(h.snap.data)
	}
	upstream := map[string]any{
		"connected": h.upstream, "last_seq": h.lastSeq, "events": h.upstreamEvents.Load(),
		"gaps": h.upstreamGaps.Load(), "connects": h.upstreamConnects.Load(),
		"snapshot_seq": snapshotSeq, "snapshot_bytes": snapshotBytes,
		"snapshots_fetched": h.snapshotsFetched.Load(),
		"ring":              map[string]int{"buffered": h.ringLen, "capacity": len(h.ring)},
	}
	h.mu.Unlock()
	lastError, _ := h.lastError.Load().(string)
	return map[string]any{
		"mode":           "fanout",
		"upstream":       upstream,
		"clients":        clients,
		"clients_served": h.clientsServed.Load(),
		"bus": map[string]any{
			"enabled": h.bus != nil, "published": h.busPublished.Load(), "errors": h.busErrors.Load(),
		},
		"last_error": lastError,
	}
}

func (h *streamHub) connected() bool {
	h.mu.Lock()
	defer h.mu.Unlock()
	return h.upstream
}
