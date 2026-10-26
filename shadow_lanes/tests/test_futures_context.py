from datetime import datetime,date,timedelta
from shadow_lanes import source
from shadow_lanes.futures_context import rows_from_ticks,supplement
NOW=datetime(2026,10,26,14,50,tzinfo=source.IST)

def test_exact_master_current_session_levels_only():
 universe=[{'canonical':'NSE:SBIN26NOVFUT','exchange':'NSE','underlying':'SBIN','upstox':'NSE_FO|fixture','expiry_ms':int(datetime(2026,11,24,tzinfo=source.IST).timestamp()*1000)}]
 ticks=[('NSE:SBIN26NOVFUT',NOW,100.,1000,200),('NSE:invented',NOW,100.,1000,200),('NSE:SBIN26NOVFUT',NOW-timedelta(days=1),100.,1000,200)]
 rows=rows_from_ticks(universe,ticks,NOW)
 assert len(rows)==1 and rows[0][1]=='SBIN' and rows[0][3]=='NSE_FO|fixture'
 assert rows[0][4:7]==(100.,200,1000) and rows[0][-1]==NOW

def test_twelve_day_gap_is_not_one_day_oi_direction():
 rows=[(date(2026,10,26),date(2026,11,24),100.,1000,200,NOW,NOW,'hub_observed_futures_ticks'),(date(2026,10,14),date(2026,11,24),90.,500,100,None,NOW-timedelta(days=12),'upstox')]
 class Cursor:
  def __enter__(self):return self
  def __exit__(self,*args):pass
  def execute(self,*args):pass
  def fetchall(self):return rows
 class Connection:
  def cursor(self):return Cursor()
 result=supplement(Connection(),'SBIN',NOW)
 assert result['futures.level_oi']==1000 and result['futures.previous_session_comparable'] is False
 assert result['futures.d_oi_pct'] is None and result['futures.oi_z'] is None
 assert result['futures.contiguous_prior_sessions']==0
