"""Deterministic cost, timing, persistence and isolation checks; no network."""
import json
import subprocess
import sys
import tempfile
from pathlib import Path

from live_paper_v8_be import configure, POLICY
from dashboard_history import paper_history
from monitor_dashboard import paper_view


def run(symbol):
    paper = configure(symbol)
    assert paper.STATE_FILE.name == f"v8_{symbol.lower()}_be_paper_state.json"
    assert paper.EVENT_FILE.name == f"v8_{symbol.lower()}_be_paper_events.jsonl"
    assert paper.SYMBOL == symbol + "USDT"
    assert paper.PROFILE['setup_expiry_candles'] == (2 if symbol == 'ADA' else 6)
    with tempfile.TemporaryDirectory() as folder:
        root = Path(folder)
        paper.STATE_FILE = root / paper.STATE_FILE
        paper.EVENT_FILE = root / paper.EVENT_FILE
        for side in ('LONG', 'SHORT'):
            long = side == 'LONG'
            setup = dict(side=side, entry=100, stop=99 if long else 101)
            p = paper.build_position(setup)
            assert p['exit_policy'] == POLICY and p['qty'] == 1
            assert p['tp2'] == (102 if long else 98)
            c = dict(timestamp=60000, open=100, high=101.2 if long else 100.5,
                     low=99.5 if long else 98.8, close=100.9 if long else 99.1)
            # Touch on either side of the future BE stop in this candle must
            # not retroactively stop the trade: the new stop starts next bar.
            assert paper.check_position(p, c) is None
            assert p['be_armed'] and p['remaining'] == p['qty']
            paper.save_state(dict(paper.default_state(), position=p))
            restored = paper.load_state()['position']
            assert restored == p
            state = dict(paper.default_state(), position=restored)
            paper.process_candle(state, dict(timestamp=120000, high=101, low=99, close=100))
            assert state['position'] is None
            assert abs(state['stats']['net_pnl_usdt'] - 0.01) < 1e-10
            assert state['stats_by_exit_policy'][POLICY]['trades'] == 1
            h = paper_history(root, f'v8_{symbol.lower()}_be')['trades'][0]
            assert h['rr'] == 2 and h['fees'] > 0 and h['funding'] == 0
            assert h['reason'] == 'Kostendeckender Break-even-Stop erreicht'
            assert h['stop'] == setup['stop'] and h['exit'] == p['stop']
            view = paper_view(dict(paper.default_state(), position=p), f'v8_{symbol.lower()}_be')
            assert view['experiment'] and 'tp1' not in view['position']
            # If original stop and +1R/target both hit, original stop wins.
            p = paper.build_position(setup)
            assert paper.check_position(p, dict(timestamp=1, high=103, low=97, close=100)) == 'STOP'
            assert not p['be_armed']
            # Target is full-size, never a partial close or BE move.
            p = paper.build_position(setup)
            c = dict(timestamp=1, high=102.1 if long else 100.1,
                     low=99.9 if long else 97.9, close=102 if long else 98)
            assert paper.check_position(p, c) == 'TP2' and p['remaining'] == 0
            # +1R with insufficient room to cover costs must not arm BE.
            p = paper.build_position(dict(side=side, entry=100, stop=99.96 if long else 100.04))
            c = dict(timestamp=1, high=100.05 if long else 100.01,
                     low=99.99 if long else 99.95, close=100.045 if long else 99.955)
            assert paper.check_position(p, c) is None and not p['be_armed']
        assert not (root / 'v8_paper_state.json').exists()
        assert not (root / 'v8_ada_paper_state.json').exists()
    print(symbol, 'BE tests passed: long/short costs, no partial exits, conservative timing, tight stops, restart, separate history.')


if __name__ == '__main__':
    if len(sys.argv) > 1:
        run(sys.argv[1])
    else:
        for symbol in ('BTC', 'ADA'):
            subprocess.run([sys.executable, __file__, symbol], check=True)
