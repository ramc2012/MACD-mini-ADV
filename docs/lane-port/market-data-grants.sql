-- Run only at the approved cutover on nomadcurie. The existing mi role never receives book-write privileges.
BEGIN;
CREATE TABLE IF NOT EXISTS macd_lane_ingest_state (
  instrument text PRIMARY KEY, session date NOT NULL, cum_volume bigint NOT NULL);
GRANT SELECT,INSERT,UPDATE ON macd_lane_ingest_state TO mi;
GRANT SELECT ON underlying_spot_candles,option_premium_candles,fo_underlying_catalog,fo_contract_catalog,
  sector_taxonomy,features_flow,sector_rs,regime,timing,leadlag,option_premium_ratios,
  vanguard_rank_model_versions,vanguard_swing_watchlist_runs,option_iv,iv_surface,market_sentiment,
  participant_oi,oi_positioning,ingest_log,bhavcopy_delivery,bulk_block,announcements,
  usdinr_daily,cross_asset_beta,features_mp TO mi;
GRANT INSERT,UPDATE ON underlying_spot_candles,option_premium_candles,option_iv,option_premium_ratios,
  iv_surface,market_sentiment,features_flow,regime,sector_rs,leadlag,timing,participant_oi TO mi;
GRANT UPDATE ON sector_taxonomy TO mi;
GRANT INSERT ON fo_contract_catalog TO mi;
GRANT UPDATE(lot_size,tick_size,updated_at) ON fo_contract_catalog TO mi;
-- Deliberately no grants on orders, fills, tickets, positions, outcomes, funded accounts or frozen watchlist items.
COMMIT;
