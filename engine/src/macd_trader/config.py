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
    # Entry gate (entry_scorer.py): score each option signal, SIZE it by its score, spend cash best-first.
    # No score cut-offs (owner, 26 Oct 2026): the weakest of the day's signals buys macd_entry_min_size_fraction
    # of the normal entry notional, the strongest all of it, never below one lot. The skip fraction now only sets
    # the reference quantile journalled beside each score.
    macd_entry_filter: bool = True
    macd_entry_skip_fraction: float = Field(default=0.30, ge=0.0, le=0.5)
    macd_entry_min_size_fraction: float = Field(default=0.25, gt=0.0, le=1.0)
    macd_entry_rank_seconds: float = Field(default=15.0, ge=0.0, le=300.0)
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
    # Paper fills cross the quoted spread: a BUY fills at the ask and a SELL at the bid when the tick that
    # carried the fresh price also carried a two-sided quote; otherwise last price +/- slippage_bps. At
    # 5 bps a round trip cost 0.1%, while the median stock-option spread at blast entries was 1.9% of mid
    # (index options 0.25%), so every stock-option round trip read ~1.8 pp better than it could fill
    # (research/option_recs_20261015). A side never fills more than paper_max_half_spread_pct beyond the
    # last price, so a one-sided or junk quote cannot book a fiction in the other direction.
    paper_cross_spread: bool = True
    paper_max_half_spread_pct: float = Field(default=5.0, ge=0.0, le=50.0)
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
    # Daily automatic login: the refresh token Fyers returns with an access token (valid ~15 days) and the account
    # PIN it is exchanged with. Both set through the login flow / the platform's Brokers page; never shown.
    fyers_refresh_token: str = ""
    fyers_pin: str = ""
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
    # Sector and broad-market indices kept on the feed for context only (the ATM heat map's Spot view, to see
    # which sectors are moving). Never traded. Each is validated with one quotes call at connect and only names the
    # broker prices are subscribed, so an unknown symbol cannot break the stream. The first nine are the names the
    # Nomad Curie app already streams from Fyers; the last four follow the same pattern and are kept if they price.
    context_indices_csv: str = (
        "NSE:NIFTYIT-INDEX,NSE:NIFTYAUTO-INDEX,NSE:NIFTYPHARMA-INDEX,NSE:NIFTYFMCG-INDEX,NSE:NIFTYMETAL-INDEX,"
        "NSE:NIFTYENERGY-INDEX,NSE:NIFTYREALTY-INDEX,NSE:FINNIFTY-INDEX,NSE:NIFTYNXT50-INDEX,"
        "NSE:NIFTYPSUBANK-INDEX,NSE:NIFTYPVTBANK-INDEX,NSE:NIFTYMEDIA-INDEX,NSE:NIFTYINFRA-INDEX")

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
    # Owner, 28 Oct 2026: "convert blast lane with this strategy" -- HMA(20) crossing above KAMA(10,2,30) on 30-minute
    # premium bars enters; HMA back below KAMA on a closed bar, a blast_hard_stop_pct stop or the 4th session's 15:00
    # exits; no trail (research/hma_premium_20261028). "macd_screen" restores the original screen and trail.
    blast_strategy: Literal["hma_kama", "macd_screen"] = "hma_kama"
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
    # The base premium/breadth rules apply at this VIX. Scale is bounded
    # between 0.5x and 2x. Higher VIX lowers breadth (user policy, 13 Oct).
    blast_vix_adaptive_enabled: bool = True
    blast_vix_reference: float = Field(default=20.0, ge=5.0, le=100.0)
    blast_vix_breadth_adjustment: float = Field(default=-0.10, ge=-0.25, le=0.25)
    blast_vix_max_age_seconds: float = Field(default=900.0, ge=30.0, le=3600.0)
    # A richer premium can pass up to this multiple of the VIX-adjusted
    # normal limit, with ticket size reduced by inverse richness squared.
    blast_rich_premium_max_multiple: float = Field(default=2.0, ge=1.0, le=4.0)
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
    # The bar the MACD(12,26,9) signal-line cross is judged on. 1800 = closed
    # 30-minute bars (09:15-aligned), the lean default: on one-minute bars the
    # replay of 16-29 Sep took 2,792 trades against 114, for a lower profit
    # factor (1.12 vs 1.37) and a result that was negative without its best
    # 1% of trades. 60 restores the one-minute screen exactly. The recent-high
    # window and history minimum stay in minutes whatever this is.
    blast_signal_bar_seconds: int = Field(default=1800, ge=60, le=3600)
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

    # Smart lanes: independent paper books, one per capital. No Qwen dependency.
    # MACD-lane signals: re-checked smart_entry_delay_seconds after the cross,
    # prioritized locally using research rank and current shared market context,
    # sized at smart_ticket_fraction of equity scaled by dispersion; exits at a
    # 40% stop, a 30% trail from +40%, all out at +100%, staged exits, MACD
    # invalidation and a 4-session hold limit. Blast-screen candidates are the
    # optional second source, disabled by default. smart_skip_fraction is a
    # historical reference only; weak scores size down rather than veto entries.
    smart_lanes_enabled: bool = True
    # Book profitable index-option positions in the MACD and smart lanes on extreme index moves
    # (extreme_booking.py; research/extreme_moves_20261014).
    extreme_booking_enabled: bool = True
    smart_lane_capitals_csv: str = "100000,5000000"
    # Owner, 28 Oct 2026: "Limit positions to upto 3 in 1L lane to maintain size with each position sized proportional
    # to available capital." capital:slots pairs; a lane with slots sizes each entry at available cash / free slots
    # (times side breadth) instead of smart_ticket_fraction of equity, and takes no entry while every slot is used.
    smart_lane_slots_csv: str = "100000:3,5000000:25"
    smart_ticket_fraction: float = Field(default=0.10, gt=0, le=1)
    smart_entry_delay_seconds: int = Field(default=300, ge=0, le=3600)
    smart_rank_seconds: float = Field(default=15.0, ge=0, le=300)
    smart_skip_fraction: float = Field(default=0.30, ge=0, lt=1)
    # No score cut-off (owner, 26 Oct 2026): the ranker's percentile scales the ticket from this share up to 1.
    # 1 (28 Oct 2026) sizes every signal alike: live, the ranker's lowest fifth did best and its top fifth worst.
    smart_min_score_size: float = Field(default=1.0, gt=0, le=1)
    smart_hard_stop_pct: float = Field(default=0.40, gt=0, lt=1)
    smart_trail_activation_pct: float = Field(default=0.40, gt=0, lt=5)
    smart_trail_pct: float = Field(default=0.30, gt=0, lt=1)
    smart_target_pct: float = Field(default=1.00, gt=0, le=20)
    smart_max_hold_sessions: int = Field(default=4, ge=0, le=60)
    smart_min_dispersion_size: float = Field(default=0.25, gt=0, le=1)
    smart_blast_source: bool = False

    # Spot MACD lane (spot_lane.py; owner, 3 Nov 2026: "a positional trading system with macd rules but to trade
    # spot with ticket size of 1L"). Long-only cash equities on the MACD lane's own EQ signals (30-min MACD zero
    # cross up), one ticket per stock, carried overnight. Exits are the MACD lane's ladder scaled to spot by 1/10:
    # 3% stop, a third out at +3/+5/+7.5%, 2.5% trail from +3% (breakeven floor), MACD-below-zero close while the
    # best was under +1%. Owner, 3 Nov 2026 (later): "start with 50L capital. hold time 3-5 days" -- for the first
    # spot_min_hold_sessions only the hard stop can close a position (the rest of the ladder waits), and it is out at
    # 15:25 on the spot_max_hold_sessions-th session. research/spot_macd_20261103: long-only spot MACD tracked the
    # market (entry skill vs random entries positive in 2025, negative since) -- not proven.
    # Owner, 4 Nov 2026: "in futures for both long and shorts with exchange specfied lot size" -- the stock's own
    # 30-min MACD crossing zero picks the side (up long, down short) and the trade is the stock's future,
    # spot_lane_lots exchange lots, the nearest series that outlives the hold. Capacity is margin: a position
    # blocks spot_futures_margin_pct of its notional (an estimate of SPAN + exposure, not the exchange's figure).
    # spot_lane_ticket is no longer used for sizing.
    spot_lane_enabled: bool = True
    spot_lane_lots: int = Field(default=1, ge=1, le=50)
    spot_futures_margin_pct: float = Field(default=0.25, gt=0, le=1)
    spot_short_enabled: bool = True
    spot_lane_capital: float = Field(default=5_000_000.0, gt=0)
    spot_lane_ticket: float = Field(default=100_000.0, gt=0)
    spot_hard_stop_pct: float = Field(default=0.03, gt=0, lt=1)
    spot_trail_activation_pct: float = Field(default=0.03, gt=0, lt=5)
    spot_trail_pct: float = Field(default=0.025, gt=0, lt=1)
    spot_staged_exits_csv: str = "0.03,0.05,0.075"
    spot_invalidation_max_mfe_pct: float = Field(default=0.01, ge=0, lt=1)
    spot_min_hold_sessions: int = Field(default=3, ge=0, le=250)
    spot_max_hold_sessions: int = Field(default=5, ge=0, le=250)

    # Agentic lane (agentic_lane.py): a paper options book run by a Grok model that opens, adds, reduces,
    # closes and re-levels positions every cycle with written reasoning. The API key comes from the
    # XAI_API_KEY environment variable (never a setting); without it the lane only manages open positions.
    agentic_enabled: bool = True
    agentic_capital: float = Field(default=1_000_000.0, gt=0)
    agentic_model: str = "grok-4.7"
    agentic_api_base: str = "https://api.x.ai/v1"
    agentic_cycle_minutes: int = Field(default=15, ge=5, le=120)
    agentic_max_positions: int = Field(default=10, ge=1, le=40)     # intraday and swing books together
    agentic_max_position_pct: float = Field(default=0.15, gt=0, le=1)
    agentic_max_deployed_pct: float = Field(default=0.60, gt=0, le=1)
    agentic_daily_loss_stop_pct: float = Field(default=0.03, gt=0, le=1)
    agentic_max_lots_per_order: int = Field(default=20, ge=1, le=500)

    # Raw tick capture. Ticks cannot be backfilled -- Fyers' finest history is
    # 5-second OHLCV -- so the socket's output is the only copy that will ever
    # exist. Raw rows are kept this many days, then condensed to the flow and
    # volume-profile facts a candle cannot reproduce.
    # Live OI (oi_capture.py): the minute chain snapshots cover these index roots beside NIFTY/BANKNIFTY, and
    # every F&O stock's chain is swept once per window (0 turns the stock sweep off).
    chain_extra_roots: list[str] = ["SENSEX", "MIDCPNIFTY", "FINNIFTY", "NIFTYNXT50", "BANKEX", "FOCIT", "NIFTYFPI"]
    stock_chain_sweep_minutes: int = Field(default=15, ge=0, le=120)
    # The permanent market-data archive (archive.py): a host folder mounted into the containers, so a Docker
    # reset cannot take it. Raw ticks are kept there tick_archive_retention_days; every other dataset forever.
    # The SQLite stores below are the hot window: nothing leaves them until the archive holds it.
    # The market-intelligence hub (~/CLAUDE PROJECTS/market-intelligence): when set, every Fyers data REST call goes through its
    # shared budget and cache (brokers.FyersBroker._data_get), straight to Fyers for a minute whenever the hub fails.
    # Empty = straight to Fyers. Environment only (MACD_MARKET_HUB_URL, engine and desk): a "" left in
    # settings.json silently kept both processes off the hub until 4 Nov 2026 (owner: route through the hub).
    market_hub_url: str = ""
    # Entry-time order flow: "desk" reads the desk's published snapshots (live_features.FlowSnapshots) and runs no
    # tracker here; the engine's own tracker runs only while no snapshot has arrived. "local" = the old tracker.
    entry_flow_source: str = "desk"
    archive_dir: str = "/app/archive"
    tick_archive_retention_days: int = Field(default=120, ge=0, le=3650)
    tick_capture_enabled: bool = True
    tick_retention_days: int = 5
    # Derived flow is ~300x smaller per session than the ticks it comes from,
    # so it keeps a research year while the raw tier stays a replay window.
    flow_retention_days: int = 365
    tick_database_path: str = str(RUNTIME_DIR / "ticks.sqlite3")
    # Minute-bar cache (historical.sqlite3) retention, pruned outside market
    # hours: an option's bars go this many days after its expiry, and any bar
    # older than history_retention_days goes. 0 disables a rule.
    history_retention_days: int = Field(default=120, ge=0, le=3650)
    expired_contract_retention_days: int = Field(default=30, ge=0, le=3650)
    # Process layout. "all" runs every lane in one process (the original
    # layout). "strategy" owns the Fyers feed, the MACD and blast lanes, and
    # serves the desk; "desk" runs the Market Profile / order-flow lane from
    # the tick bus. Environment-only, like the paths below.
    engine_role: Literal["all", "strategy", "desk"] = "all"
    # Tick bus (NATS). The strategy/all process publishes every accepted tick;
    # the desk subscribes. Empty disables publishing.
    nats_url: str = ""
    strategy_url: str = "http://engine:8100"
    desk_url: str = ""

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
    # Owner, 28 Oct 2026: a selected contract that is not trading is replaced by an active one around the live
    # price. Every contract_refresh_minutes inside 09:45-14:30 IST, a spot whose ATM leg has had no trade for
    # contract_inactive_minutes, or whose live price sits contract_refresh_max_steps strikes or more from the ATM
    # strike, gets its ladder selected again from the master and live quotes; the library and feed are updated
    # and held contracts stay subscribed. 0 minutes turns it off.
    contract_refresh_minutes: int = Field(default=30, ge=0, le=240)
    contract_inactive_minutes: int = Field(default=15, ge=1, le=240)
    contract_refresh_max_steps: float = Field(default=2.0, gt=0, le=20)
    # Stock options leave a series on its last day: this many trading sessions before expiry (T-2 for a
    # Tuesday expiry is the Friday). From that day new risk goes to the next series and anything still held
    # is closed by expiry_flatten_ist (at once if the day has passed). Index options are unaffected. 0 = off.
    stock_option_exit_sessions: int = Field(default=2, ge=0, le=5)
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
    def context_indices(self) -> list[str]:
        return [s.strip() for s in self.context_indices_csv.split(",") if s.strip()]

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


# Paths, the process layout and secrets come from the environment (and
# credentials.json), never from settings.json: a stored tick_database_path used
# to override the one Compose set, so moving a database silently did nothing.
ENVIRONMENT_ONLY = frozenset({
    "database_path", "runtime_settings_path", "credentials_path", "research_database_path",
    "research_report_path", "contract_snapshot_path", "mp_database_path",
    "blast_database_path", "tick_database_path", "api_token", "allowed_origins_csv",
    "engine_role", "nats_url", "strategy_url", "desk_url", "market_hub_url",
})
