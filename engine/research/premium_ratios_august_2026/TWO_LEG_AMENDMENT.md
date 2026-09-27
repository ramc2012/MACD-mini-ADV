# Coverage-driven amendment — before reading forward outcomes

31 August 2026, approximately 07:48 IST. The full-ladder run found no eligible
three-strike CE or PE ladder across 7,268 side/expiry/day selection attempts.
A separate signal-time probe found 3,879 side-days with one positive-volume
quote and 1,429 with two, and none with three or more at 09:44. Therefore the
original full-ladder primary study is UNAVAILABLE, not a negative backtest.

Proceed with an explicitly narrower test of actual ITM/OTM ratios. At 09:45,
select the nearest quoted strike strictly below and strictly above spot, each
within 2% of spot. For CE the lower strike is ITM; for PE the higher strike is
ITM. Freeze both for that session. Only their actual premium-close ratio and
its log change enter ratio models. No synthetic ATM or other ratio is created.

For price controls and hypothetical cost sensitivity, the nearer of these two
quoted legs is an explicitly labeled near-ATM PROXY. It is not proof that the
exchange's nearest ATM contract was archived. Trade results refer to that
actual quoted contract, not a fabricated premium.

All original train/validation/test dates, next-bar outcome rules, quality
filters, model parameters, baselines, cost scenarios and robustness checks are
unchanged. Outputs go under `two_leg/`, separate from full-ladder coverage.
Paired CE+PE 5m/30m remains the primary comparison for this narrower study if
its training coverage passes. This amendment responds only to missing input
data, not to a forward performance result.

Post-run diagnostic: added a training-majority constant direction benchmark and
reported predicted-up/actual-up proportions to identify class imbalance. This
did not change any ratio feature, model coefficient, score, or cutoff.

Upstox extension requested by user: Nomad Curie's saved credentials were found
and read in memory only. Docker-side access hit a DNS error. Host-side normal
and expired-history requests were rejected by Cloudflare error 1010, HTTP 403,
`browser_signature_banned`, `retryable=false`, `owner_action_required=true`.
Stopped requests after inspecting that explicit denial; no identity/header
spoofing or access-rule bypass was attempted. No August data was downloaded.
The stored OAuth JWT's expiry claim was not expired, but the denial prevents
confirming API authentication or expired-instrument entitlement. Do not infer
a missing Plus subscription from this network denial.
