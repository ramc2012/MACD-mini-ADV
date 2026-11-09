"""9 Nov 2026: two FYERS websockets on one SDK object cross-wired ~975 option contracts' prices from 13:10 IST."""
import threading
import time

from macd_trader.brokers import _retire_sdk_socket, _serialise_sdk_reconnects


class FakeWs:
    def __init__(self):
        self.closed = False

    def close(self):
        self.closed = True


class FakeSocket:
    """Mimics the SDK: a singleton whose connect() opens a websocket that registers itself only once open."""
    _instance = None
    opened = 0

    def __new__(cls, *a, **k):
        if not cls._instance:
            cls._instance = super().__new__(cls)
        return cls._instance

    def __init__(self):
        self.restart_flag = True
        self._FyersDataSocket__ws_object = None

    def connect(self):
        if self._FyersDataSocket__ws_object is None:
            FakeSocket.opened += 1
            time.sleep(0.05)                       # the socket registers itself a moment later
            self._FyersDataSocket__ws_object = FakeWs()

    def subscribe(self, **_):
        raise AssertionError("retired socket subscribed")


def test_retiring_gives_a_fresh_object_and_kills_a_sleeping_reconnect():
    FakeSocket._instance = None
    old = FakeSocket()
    old.connect()
    ws = old._FyersDataSocket__ws_object
    _retire_sdk_socket(FakeSocket)
    assert ws.closed and old.restart_flag is False and FakeSocket._instance is None
    before = FakeSocket.opened
    old.connect(); old.subscribe(symbols=["X"])          # what a reconnect waking inside the old object would do
    assert FakeSocket.opened == before
    assert FakeSocket() is not old


def test_two_simultaneous_reconnects_open_one_socket():
    FakeSocket._instance = None
    sock = FakeSocket()
    _serialise_sdk_reconnects(sock)
    before = FakeSocket.opened
    threads = [threading.Thread(target=sock.connect) for _ in range(2)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert FakeSocket.opened == before + 1


def test_a_later_reconnect_after_the_window_still_opens():
    FakeSocket._instance = None
    sock = FakeSocket()
    _serialise_sdk_reconnects(sock, window=0.02)
    before = FakeSocket.opened
    sock.connect()
    sock._FyersDataSocket__ws_object = None               # that socket closed
    time.sleep(0.03)
    sock.connect()
    assert FakeSocket.opened == before + 2


def test_retire_without_instance_is_a_no_op():
    FakeSocket._instance = None
    _retire_sdk_socket(FakeSocket)
