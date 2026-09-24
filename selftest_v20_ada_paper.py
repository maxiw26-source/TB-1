"""Offline checks for V20 state, forward execution and dashboard schema."""
import tempfile
from pathlib import Path
from unittest.mock import patch
import live_paper_v20_ada as bot
import monitor_dashboard as dashboard


def bar(ts,low=99,high=101):
    return dict(timestamp=ts,open=100,close=100,low=low,high=high,volume=1)


def main():
    bot.strategy.selftest()
    h=bot.HOUR_MS;f=bot.FIVE;t=100*h
    with tempfile.TemporaryDirectory() as folder, patch.object(bot,'STATE',Path(folder)/'state.json'):
        with patch.object(bot.strategy,'signals',return_value={t:(1,98,2,99)}):
            s=bot.load_state()
            bot.step(s,[bar(t-f)],100,t+1000)
            assert s['position'] is None # initial start never backfills
            s['last_closed_ts']=t-2*f
            bot.step(s,[bar(t-f)],100,t+1000,allow_entry=False)
            assert s['position'] is None # restart never enters historical signal
            s['last_closed_ts']=t-2*f
            bot.step(s,[bar(t-f)],100,t+1000)
            assert s['position'] and abs(s['position']['entry']*s['position']['qty']-100)<1e-9
            bot.save(s);assert bot.load_state()==s
            bot.step(s,[bar(t-f),bar(t,low=90,high=110)],100,t+f+1000)
            assert s['position'] # partial entry candle cannot trigger pre-entry extremes
            bot.step(s,[bar(t-f),bar(t),bar(t+f,low=90,high=110)],100,t+2*f+1000)
            assert s['position'] is None and s['stats']['trades']==1
            assert s['stats']['net_usdt']<0 and s['stats']['fees_usdt']>0
            bot.step(s,[bar(t-f),bar(t),bar(t+f)],100,t+2*f+2000)
            assert s['stats']['trades']==1 # repeated poll no double settlement
            s['last_closed_ts']=t-2*f
            bot.step(s,[bar(t-f)],100,t+1000)
            assert s['position']
            bot.step(s,[bar(t+f)],100,t+2*f+1000)
            assert s['halted'] and s['position'] and s['stats']['trades']==1
            report=dashboard.paper_view(s,'v20_ada')
            assert report['halted'] and report['error'] and report['pnl']==round(s['stats']['net_usdt'],4)
    print('V20 PAPER SELFTEST OK: 100 USDT, Start/Restart, State, Teilkerze, Stop-first, Kosten, Datenluecke, Dashboard')


if __name__=='__main__':
    main()
