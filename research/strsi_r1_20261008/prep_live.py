import pandas as pd
s = pd.read_csv('/w/out/spot_live.csv'); c = pd.read_csv('/w/out/spot_ctrl_live.csv')
sig = s[s.set == 'ST+RSI70+R1'].assign(kind='signal', group=lambda d: d.entry_ts)
ctl = c.groupby('event_ts', group_keys=False).apply(lambda g: g.sample(min(3, len(g)), random_state=7)).assign(kind='control', group=lambda d: d.event_ts)
cols = ['kind', 'group', 'sym', 'day', 'entry_ts', 'entry', 'st_exit_ts']
pd.concat([sig[cols], ctl[cols]]).to_csv('/w/out/in_events_live.csv', index=False)
print(len(sig), len(ctl))
