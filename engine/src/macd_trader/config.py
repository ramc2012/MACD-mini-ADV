import os
from pathlib import Path
from typing import Literal

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


ROOT = Path(__file__).resolve().parents[2]
RUNTIME_DIR = Path(os.getenv("MACD_RUNTIME_DIR", str(Path.cwd() / "runtime")))


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=(str(ROOT / ".env"),),
        env_prefix="MACD_",
        extra="ignore",
        populate_by_name=True,
    )

    app_name: str = "MACD Trader"
    feed_mode: Literal["simulation", "fyers"] = "simulation"
    execution_mode: Literal["paper", "live"] = "paper"
    allow_live_orders: bool = False
    symbols_csv: str = ""
    timeframe_seconds: int = 60
    fast_period: int = 12
    slow_period: int = 26
    signal_period: int = 9
    bb_period: int = 20
    bb_deviations: float = 2.0
    kama_period: int = 10
    kama_fast: int = 2
    kama_slow: int = 30
    kama_rsi_period: int = 14
    # A KAMA-RSI of 65 and a positive half-percent 5-bar ROC were the
    # least-lossy sufficiently-sized filter in the August paper comparison.
    # They are intentionally settings, not hard-coded signal constants.
    kama_rsi_min: float = 65.0
    kama_roc_period: int = 5
    kama_roc_min: float = 0.5
    # The MACD move from <= 0 to > 0 is always the entry trigger. These
    # switches optionally require the older confirmation filters as well.
    # They default off so a genuine gap-up zero-cross is not vetoed by a
    # lagging KAMA/RSI/ROC calculation.
    require_kama_confirmation: bool = False
    require_kama_rsi_confirmation: bool = False
    require_kama_roc_confirmation: bool = False
    entry_volume_ratio: float = 2.5
    signal_mode: Literal["zero_cross", "signal_cross", "both"] = "zero_cross"
    auto_trade: bool = False
    order_quantity: int = 1
    max_trade_lots: int = 4
    order_quote_max_age_seconds: float = Field(default=10.0, gt=0, le=60)
    # Exit a position whose entry thesis has failed BEFORE it ever worked.
    # Measured over 3-10 Sep on 109 hard-stopped positions: MACD closed back
    # below zero before the -30% stop fired in 87% of them, a median 20.8h
    # earlier. Acting on that unconditionally is a LOSS -- it also cuts 29% of
    # winning slices, -1,009,174 of forgone profit against 598,770 of avoided
    # loss. Gating it on "has never been up max_mfe_pct" removes that: no
    # winner in the sample had an MFE below 20%, because the first scale-out
    # is at +30%. At a 10% gate the rule closed 56 losers for +481,522 and
    # touched no winner. Off by default: it changes live exit behaviour.
    macd_invalidation_exit: bool = False
    macd_invalidation_max_mfe_pct: float = Field(default=0.10, ge=0.0, le=1.0)
    max_positions: int = Field(default=0, ge=0, le=1000)
    min_cash_reserve: float = Field(default=0.0, ge=0)
    # Target rupee exposure for a new paper position. Orders still use whole
    # exchange lots. Zero keeps the legacy one-lot entry behaviour.
    target_position_notional: float = 0.0
    # A low-premium option may need many exchange lots to reach the target.
    # This separate ceiling does not enlarge manual orders or pyramiding,
    # which remain governed by max_trade_lots.
    max_target_entry_lots: int = 125
    hard_stop_pct: float = 0.30
    trailing_activation_pct: float = 0.30
    trailing_stop_pct: float = 0.25
    initial_capital: float = 1_000_000.0
    slippage_bps: float = 5.0
    api_token: str = ""
    # Extra browser origins allowed to open the stream socket, comma-separated
    # (e.g. "https://desk.example.ts.net"). Loopback origins are always allowed.
    allowed_origins_csv: str = ""
    database_path: str = str(RUNTIME_DIR / "macd_trader.sqlite3")
    runtime_settings_path: str = str(RUNTIME_DIR / "settings.json")
    credentials_path: str = str(RUNTIME_DIR / "credentials.json")
    research_database_path: str = str(RUNTIME_DIR / "historical.sqlite3")
    research_report_path: str = str(RUNTIME_DIR / "walk_forward_report.json")
    fyers_client_id: str = ""
    fyers_secret: str = ""
    fyers_access_token: str = ""
    # Risk-free rate used to solve implied volatility for GEX (India ~6.5%).
    risk_free_rate: float = 0.065

    # Market Profile / Order Flow desk (independent book).
    mp_enabled: bool = True
    mp_auto_trade: bool = False
    mp_initial_capital: float = 1_000_000.0
    # Four concurrent positions committed only ~4 lakh of the desk's 10 lakh
    # at the configured 1 lakh notional cap, and "max_positions reached" was
    # the desk's binding constraint rather than any signal. Eight still leaves
    # ~2 lakh of headroom. Deliberately raised risk, editable from the panel.
    # DEPLOY STEP: this is only the default. runtime/settings.json already pins
    # mp_max_positions to 4 and is loaded over it, so merging alone leaves the
    # live desk rejecting at 4 — the owner has to open Settings → Strategy →
    # Market Profile desk limits and Save once for the new cap to take effect.
    mp_max_positions: int = 8
    mp_max_trades_per_day: int = 12
    mp_hard_stop_pct: float = 0.25
    mp_trail_activation_pct: float = 0.20
    mp_trail_pct: float = 0.20
    mp_min_imbalance: float = 0.25
    mp_slippage_bps: float = 25.0
    mp_brokerage_per_leg: float = 20.0
    # Keep a non-expiring MP position overnight only when both the closing
    # auction location and signed order flow still confirm its direction.
    # Stops and auction-reversal exits remain active regardless.
    mp_allow_overnight_carry: bool = False
    # The desk trades options only, one lot at a time, capped by notional.
    # A bullish read buys the ATM call and a bearish read the ATM put, so it
    # takes both directions without ever shorting.
    mp_max_notional_per_trade: float = 100_000.0
    mp_database_path: str = str(RUNTIME_DIR / "mp_trader.sqlite3")
    # The auction desk's own spot scope, independent of the MACD lane. The MACD
    # lane trades the full F&O universe; this desk is deliberately narrow
    # because its trade-by-trade feed is capped at roughly 15 instruments
    # (5 symbols per connection, 3 connections). Empty means "everything".
    # Futures tickers here may name a dead series, or drop it entirely
    # ("NSE:NIFTY-FUT"); either way the active series is resolved daily from
    # the Fyers derivatives master.
    mp_symbols_csv: str = ""
    # India VIX, for the session badge's implied daily range (spot × VIX/100 ÷
    # √252). Fyers streams it as an index -- no volume, no book -- so it is a
    # quote to read, never an instrument to trade; the badge shows "n/a" for
    # as long as no tick has arrived. Blank disables the lookup.
    vix_symbol: str = "NSE:INDIAVIX-INDEX"

    # --- Blast lane (third book) -----------------------------------------
    # Hunts the option moves the MACD lane cannot reach. Its entry is the
    # MACD/signal-line cross, NOT the zero-cross: inside the screen below, 98%
    # of qualifying candidates (10,544 of 10,739 over Jun-Sep 2026) have MACD
    # still under zero, so the zero-cross lane never sees them.
    #
    # Thresholds are the ones frozen before the September walk-forward, taken
    # from the Jun-Aug bins and not re-touched afterwards. On 8 unseen sessions
    # the screen lifted the probability of a contract doubling within two
    # sessions from 7.9% to 17.5% and turned both exit policies positive -- but
    # on 57 picks, at +5.7% per candidate with a 95% interval of [-1.4, +18.1]
    # and permutation p=0.080. Direction established, size not. Hence
    # blast_auto_trade defaults OFF: the lane journals every candidate and
    # trades none until the owner turns it on.
    blast_enabled: bool = True
    blast_auto_trade: bool = False
    blast_initial_capital: float = 1_000_000.0
    # Raise (with blast_initial_capital) to run the lane unbounded, taking
    # every passing signal, when the point is to measure the screen.
    blast_max_positions: int = 10
    blast_target_notional: float = 100_000.0
    blast_max_entry_lots: int = 125
    # Premium as a percentage of the UNDERLYING's price. This is the load-
    # bearing filter and it is deliberately a ratio: raw rupee premium looked
    # predictive in an earlier cut because it was standing in for this, which
    # made a 1-rupee option on a 20-rupee stock look like a 1-rupee option on a
    # 2000-rupee one. Walk-forward blast rate: 14.6% at <=1.2%, 9.9% at <=2.0%,
    # 7.9% unfiltered. Dropping this leg halves the blast rate.
    blast_max_premium_pct: float = Field(default=1.2, gt=0, le=100)
    # Fraction of tracked contracts on the SAME side (CE or PE) whose premium
    # MACD is above zero. Below 0.50 the trade is fighting the tape.
    blast_min_breadth: float = Field(default=0.50, ge=0.0, le=1.0)
    # Minimum contracts on a side before that side's breadth is trusted at all.
    # A fraction read off three contracts is noise, and the screen would then
    # be gated on nothing.
    blast_min_breadth_cohort: int = Field(default=20, ge=1, le=5_000)
    # How far below its own recent high the premium must be, in percent.
    blast_min_off_high_pct: float = Field(default=25.0, ge=0.0, le=99.0)
    # Ceiling on an entry's size as a share of the contract's own liquidity,
    # measured the way the ladder measures it: max(volume, oi/100) from the
    # selection-time chain. A flat rupee ticket ignores what the contract
    # trades, so a cheap far-OTM leg bought 48,000 units of a 2-rupee option
    # -- 81% of everything that traded in it that day (BAJAJFINSV 1960CE,
    # 17 Sep). That fill is fiction, and a lane measured on fictional fills
    # measures nothing. Contracts too thin to carry even one lot are declined
    # as TOO_ILLIQUID rather than sized down to a fill that cannot happen.
    blast_max_volume_share_pct: float = Field(default=2.0, gt=0, le=100)
    # Length of that "recent high" window in ONE-MINUTE bars, whatever
    # timeframe_seconds is: the lane keeps its own minute bars. 750 is two
    # sessions, which is what the study measured.
    blast_high_lookback_bars: int = Field(default=750, ge=30, le=5_000)
    # One-minute bars of history required before the screen will judge a
    # contract at all (120 is two hours); a shorter window makes "off its
    # high" mean something different.
    blast_min_lookback_bars: int = Field(default=120, ge=10, le=5_000)
    # Exit overlay. Wider than the MACD lane's on purpose: screened candidates
    # average close to +100% forward excursion and the +30/+50/+75 ladder gives
    # nearly all of it back. No scale-outs and no pyramiding on this lane.
    blast_hard_stop_pct: float = Field(default=0.50, gt=0, lt=1)
    blast_trail_activation_pct: float = Field(default=0.30, gt=0, lt=5)
    blast_trail_pct: float = Field(default=0.40, gt=0, lt=1)
    # Forward tracking of journalled candidates, including the rejected ones --
    # the control group that says whether each leg of the screen is earning its
    # place. Wall-clock hours; ticks only arrive in session, so 48 hours is
    # about two sessions of observation.
    blast_journal_horizon_hours: float = Field(default=48.0, gt=0, le=720)
    blast_journal_max_watchers: int = Field(default=2_000, ge=0, le=50_000)
    blast_database_path: str = str(RUNTIME_DIR / "blast_trader.sqlite3")

    # Raw tick capture. Ticks cannot be backfilled -- Fyers' finest history is
    # 5-second OHLCV -- so the socket's output is the only copy that will ever
    # exist. Raw rows are kept this many days, then condensed to the flow and
    # volume-profile facts a candle cannot reproduce.
    tick_capture_enabled: bool = True
    tick_retention_days: int = 5
    # Derived flow is ~300x smaller per session than the ticks it comes from,
    # so it keeps a research year while the raw tier stays a replay window.
    flow_retention_days: int = 365
    tick_database_path: str = str(RUNTIME_DIR / "ticks.sqlite3")

    # Telegram alerting (bot token is a secret; empty disables alerts).
    telegram_bot_token: str = ""
    telegram_chat_id: str = ""
    # Day P&L level (rupees, positive number) that triggers a Telegram alert.
    # Zero disables the check. Alert only — nothing is halted automatically.
    day_loss_alert_rupees: float = 50_000.0
    fyers_redirect_uri: str = "https://trade.fyers.in/api-login/redirect-uri/index.html"
    contract_snapshot_path: str = str(RUNTIME_DIR / "atm_contracts.json")

    # --- Series rollover -------------------------------------------------
    # Minimum calendar days to expiry for a contract to be newly selected.
    # 1 rolls off the expiry day itself, which is the whole point: on
    # 25 Aug 2026 the front chain put 427 of 429 selected contracts hours
    # from settlement. 0 restores the previous "anything unexpired" rule.
    # Applies to the ATM option lane and to the desk's futures series alike.
    min_days_to_expiry: int = 1
    # IST time at which any position still held in a contract expiring TODAY
    # is flattened. Nothing else closes them: an expired option simply stops
    # ticking, so the paper book would carry its last mark forever. Blank
    # disables the sweep.
    expiry_flatten_ist: str = "15:10"
    # Exchange holidays beyond the built-in list in market_calendar.py, as
    # comma-separated YYYY-MM-DD dates: next year's list before a release, or
    # an unscheduled closure. Every session gate reads the combined calendar.
    market_holidays_csv: str = ""

    @property
    def symbols(self) -> list[str]:
        """The spot universe; ``symbols_csv`` narrows it when set.

        An empty setting uses the maintained F&O stock list and four index
        spots. A comma-separated value narrows the live universe.
        """
        from .universe import SPOT_SYMBOLS
        chosen = [item.strip() for item in self.symbols_csv.split(",") if item.strip()]
        return chosen or list(SPOT_SYMBOLS)

    @property
    def entry_filter_description(self) -> str:
        filters = ["MACD zero-cross up through zero"]
        if self.require_kama_confirmation:
            filters.append("rising KAMA")
        if self.require_kama_rsi_confirmation:
            filters.append("RSI(KAMA)")
        if self.require_kama_roc_confirmation:
            filters.append("ROC(KAMA)")
        return " + ".join(filters)

    @field_validator("timeframe_seconds")
    @classmethod
    def validate_timeframe(cls, value: int) -> int:
        if value < 1:
            raise ValueError("timeframe_seconds must be positive")
        return value


settings = Settings()
