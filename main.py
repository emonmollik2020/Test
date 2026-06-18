import ccxt
import pandas as pd
import ta
import time
import threading
import json
import os
from flask import Flask, render_template_string, jsonify
from datetime import datetime, timezone

# ========== 1. সেটিংস ও ডাটা ==========
SYMBOL, STATE_FILE, INITIAL_FUND = "SOL/USDT", "bot_state.json", 100.0
DEF_TP, DEF_SL = 0.007, 0.010
exchange = ccxt.bitget({'enableRateLimit': True})

def save_state(data):
    with open(STATE_FILE, "w") as f: json.dump(data, f)

def load_state():
    if not os.path.exists(STATE_FILE):
        return {"price": 0.0, "balance": 100.0, "total_pnl": 0.0, "last_update": "...", "trades": 0, "win_rate": 0.0, "best": 0.0, "last_action": "---", "in_position": False, "live_pnl_pct": 0.0, "entry_price": 0.0, "tp_level": 0.0, "sl_level": 0.0, "peak_price": 0.0, "analysis_1m": {"rsi": 0, "ema": 0, "sig": "WAIT", "pats": []}, "analysis_3m": {"rsi": 0, "macd": 0, "sig": "WAIT", "pats": []}, "wait_reason": "Loading...", "log": [], "history": []}
    with open(STATE_FILE, "r") as f:
        d = json.load(f)
        if "history" not in d: d["history"] = []
        return d

app = Flask(__name__)

# --- প্যাটার্ন ডিটেক্টর ---
def detect_pats(df):
    p = []
    if len(df) < 5: return p
    c1, c2, c3 = df.iloc[-1], df.iloc[-2], df.iloc[-3]
    b1, g1 = abs(c1['c']-c1['o']), c1['c'] > c1['o']
    b2, g2 = abs(c2['c']-c2['o']), c2['c'] > c2['o']
    if (min(c1['c'],c1['o'])-c1['l']) >= 1.8*b1: p.append({"n": "Hammer", "t": "bull"})
    if not g2 and g1 and c1['c'] >= c2['o'] and c1['o'] <= c2['c']: p.append({"n": "Bull Engulfing", "t": "bull"})
    if b1 / (c1['h']-c1['l'] if c1['h']!=c1['l'] else 0.001) > 0.85 and g1: p.append({"n": "Marubozu", "t": "bull"})
    if b1 <= ((c1['h']-c1['l'])*0.1): p.append({"n": "Doji", "t": "neut"})
    return p

# --- ট্রেডিং ইঞ্জিন ---
def bot_engine():
    in_pos, entry_p, total_pnl_acc, peak_p, total, wins = False, 0.0, 0.0, 0.0, 0, 0
    pnl_hist = [0]
    while True:
        try:
            bars1 = exchange.fetch_ohlcv(SYMBOL, '1m', limit=200)
            bars3 = exchange.fetch_ohlcv(SYMBOL, '3m', limit=200)
            df1, df3 = pd.DataFrame(bars1, columns=['t','o','h','l','c','v']), pd.DataFrame(bars3, columns=['t','o','h','l','c','v'])
            p = df1['c'].iloc[-1]
            r1, e20 = ta.momentum.rsi(df1['c']).iloc[-1], ta.trend.ema_indicator(df1['c'], 20).iloc[-1]
            r3, m_obj = ta.momentum.rsi(df3['c']).iloc[-1], ta.trend.MACD(df3['c'])
            mv, ms = m_obj.macd().iloc[-1], m_obj.macd_signal().iloc[-1]

            cur = load_state()
            live_pnl = ((p/entry_p)-1)*100 if in_pos else 0.0
            if in_pos and p > peak_p:
                peak_p = p
                cur["sl_level"], cur["tp_level"] = round(p*(1-DEF_SL),2), round(p*(1+DEF_TP),2)

            cur.update({"price": round(p,2), "last_update": datetime.now(timezone.utc).strftime("%H:%M:%S"), "in_position": in_pos, "live_pnl_pct": round(live_pnl,2), "entry_price": round(entry_p,2), "analysis_1m": {"rsi": round(r1,1), "ema": round(e20,2), "sig": "BULL" if p>e20 else "BEAR", "pats": detect_pats(df1)}, "analysis_3m": {"rsi": round(r3,1), "macd": round(mv,3), "sig": "BULL" if mv>ms else "WAIT", "pats": detect_pats(df3)}})

            if not in_pos and p > e20 and r1 < 65 and mv > ms:
                entry_p, peak_p, in_pos, total = p, p, True, total+1
                cur.update({"trades": total, "balance": 0.0, "sl_level": round(p*(1-DEF_SL),2), "tp_level": round(p*(1+DEF_TP),2)})
                cur["history"].insert(0, {"t": datetime.now().strftime("%H:%M"), "a": "BUY", "p": round(p,2), "r": "---"})
                cur["log"].insert(0, {"t": datetime.now().strftime("%H:%M"), "m": f"BUY @ ${p:.2f}"})
            elif in_pos and (p >= cur["tp_level"] or p <= cur["sl_level"]):
                in_pos = False
                p_val = (100.0/entry_p*p)-100.0
                total_pnl_acc += p_val
                pnl_hist.append(p_val)
                if p > entry_p: wins += 1
                cur.update({"balance": round(100.0+total_pnl_acc,2), "total_pnl": round(total_pnl_acc,2), "win_rate": round((wins/total)*100,1), "best": round(max(pnl_hist),2), "last_action": "SELL"})
                cur["history"].insert(0, {"t": datetime.now().strftime("%H:%M"), "a": "SELL", "p": round(p,2), "r": f"{round(live_pnl,2)}%"})
                cur["log"].insert(0, {"t": datetime.now().strftime("%H:%M"), "m": f"SELL @ ${p:.2f}"})
            
            cur["wait_reason"] = "POSITION ACTIVE" if in_pos else ("Trend Down" if p<=e20 else "Searching...")
            save_state(cur)
        except: pass
        time.sleep(10)

threading.Thread(target=bot_engine, daemon=True).start()
@app.route('/api/data')
def api(): return jsonify(load_state())
@app.route('/')
def index(): return render_template_string(UI)

UI = """
<!DOCTYPE html>
<html>
<head>
    <meta charset="UTF-8"><meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Master Bot</title><script src="https://cdn.tailwindcss.com"></script>
    <style>
        body { background: #f8fafc; font-family: 'Segoe UI', sans-serif; }
        .card { background: white; border-radius: 1rem; border: 1px solid #f1f5f9; box-shadow: 0 4px 6px -1px rgba(0,0,0,0.05); }
        .tag { border: 1px solid #dcfce7; color: #166534; padding: 2px 10px; border-radius: 99px; font-size: 10px; font-weight: 700; display: inline-block; margin: 2px; }
    </style>
</head>
<body class="p-3 text-slate-800">
<div class="max-w-md mx-auto">
    <div class="text-center mb-6"><span class="bg-green-100 text-green-700 px-4 py-1 rounded-lg text-xs font-bold border border-green-200">RUNNING</span></div>
    <div class="grid grid-cols-3 gap-2 mb-4 text-center text-[10px] font-bold text-slate-400">
        <div class="card p-3"><p>TRADES</p><p id="t" class="text-lg font-black text-slate-800">0</p></div>
        <div class="card p-3"><p>WIN RATE</p><p id="w" class="text-lg font-black text-slate-800">0%</p></div>
        <div class="card p-3"><p>PROFIT</p><p id="pnl" class="text-lg font-black text-green-600">$0.00</p></div>
    </div>
    <div class="card p-6 mb-4 text-center">
        <div class="flex justify-between items-center mb-4"><span id="pr" class="text-4xl font-black">$0.00</span><div class="text-right text-[10px] text-slate-400">Balance: <b id="bl">$100.00</b></div></div>
        <div id="pnl_display" class="hidden mb-4 p-5 border-2 border-slate-100 rounded-3xl">
            <p class="text-[10px] font-bold text-slate-400 uppercase">Live P&L</p>
            <p id="lp" class="text-4xl font-black">0.00%</p>
            <div class="flex justify-around mt-4 text-[10px] font-bold border-t pt-2"><div class="text-red-500">SL: <span id="sl">0</span></div><div class="text-green-600">TP: <span id="tp">0</span></div></div>
        </div>
        <div id="st" class="bg-orange-50 text-orange-600 p-2 rounded-xl text-[10px] font-bold uppercase tracking-widest">CONNECTING...</div>
    </div>
    <div class="card p-4 mb-4 text-[11px]"><div class="flex justify-between mb-2"><b>1M ANALYSIS</b><span id="s1" class="font-bold px-2 rounded">WAIT</span></div><p>RSI: <b id="r1">0</b> | EMA: <b id="e1">0</b></p><div id="pats1" class="mt-2"></div></div>
    <div class="card p-4 mb-4 text-[11px]"><div class="flex justify-between mb-2"><b>3M ANALYSIS</b><span id="s3" class="font-bold px-2 rounded">WAIT</span></div><p>RSI: <b id="r3">0</b> | MACD: <b id="m3">0</b></p><div id="pats3" class="mt-2"></div></div>
    <div class="card overflow-hidden h-60 mb-4"><iframe src="https://s.tradingview.com/widgetembed/?symbol=BITGET%3ASOLUSDT&interval=1&theme=light" width="100%" height="100%" frameborder="0"></iframe></div>
    <div class="card p-4 mb-4"><h3 class="font-bold text-slate-700 text-xs mb-3">TRADE HISTORY</h3><table class="w-full text-[10px]"><thead class="text-slate-400"><tr><th>Time</th><th>Action</th><th class="text-right">Price</th><th class="text-right">Result</th></tr></thead><tbody id="hb"></tbody></table></div>
    <div class="card p-4 mb-6"><h3 class="font-bold text-slate-700 text-xs mb-2">LIVE LOGS</h3><div id="lg" class="text-[10px] space-y-1"></div></div>
</div>
<script>
    async function update() {
        try {
            const r = await fetch('/api/data'); const d = await r.json();
            if(d.price > 0) {
                document.getElementById('pr').innerText = '$' + d.price;
                document.getElementById('bl').innerText = '$' + d.balance.toFixed(2);
                document.getElementById('t').innerText = d.trades;
                document.getElementById('w').innerText = d.win_rate + '%';
                document.getElementById('pnl').innerText = '$' + d.total_pnl.toFixed(2);
                document.getElementById('st').innerText = d.wait_reason.toUpperCase();
                if(d.in_position) {
                    document.getElementById('pnl_display').classList.remove('hidden');
                    document.getElementById('lp').innerText = (d.live_pnl_pct >= 0 ? '+' : '') + d.live_pnl_pct + '%';
                    document.getElementById('sl').innerText = d.sl_level; document.getElementById('tp').innerText = d.tp_level;
                    document.getElementById('lp').className = 'text-4xl font-black ' + (d.live_pnl_pct >= 0 ? 'text-green-600' : 'text-red-600');
                } else { document.getElementById('pnl_display').classList.add('hidden'); }
                document.getElementById('r1').innerText = d.analysis_1m.rsi;
                document.getElementById('e1').innerText = '$' + d.analysis_1m.ema;
                document.getElementById('s1').innerText = d.analysis_1m.sig;
                document.getElementById('r3').innerText = d.analysis_3m.rsi;
                document.getElementById('m3').innerText = d.analysis_3m.macd;
                document.getElementById('s3').innerText = d.analysis_3m.sig;
                const tag = (p) => `<span class="tag">${p.n}</span>`;
                document.getElementById('pats1').innerHTML = d.analysis_1m.pats.map(tag).join('');
                document.getElementById('pats3').innerHTML = d.analysis_3m.pats.map(tag).join('');
                document.getElementById('hb').innerHTML = d.history.slice(0,5).map(h => `<tr><td>${h.t}</td><td class="font-bold ${h.a=='BUY'?'text-green-600':'text-red-600'}">${h.a}</td><td class="text-right">$${h.p}</td><td class="text-right font-black">${h.r}</td></tr>`).join('');
                document.getElementById('lg').innerHTML = d.log.slice(0,3).map(l => `<div class="flex justify-between"><span>${l.t}</span><span>${l.m}</span></div>`).join('');
            }
        } catch(e) {}
    }
    setInterval(update, 3000); update();
</script>
</body></html>
"""

if __name__ == "__main__":
    app.run(host='0.0.0.0', port=int(os.environ.get('PORT', 10000)))
