import ccxt
import pandas as pd
import ta
import time
import threading
import json
import os
from flask import Flask, render_template_string, jsonify
from datetime import datetime, timezone

# ========== ১. সেটিংস ==========
SYMBOL = "SOL/USDT"
STATE_FILE = "bot_state.json"
INITIAL_FUND = 100.0
TP_PCT = 0.007    # ০.৭% টেক প্রফিট
SL_PCT = 0.010    # ১.০% স্টপ লস
exchange = ccxt.bitget({'enableRateLimit': True})

def save_state(data):
    with open(STATE_FILE, "w") as f:
        json.dump(data, f)

def load_state():
    if not os.path.exists(STATE_FILE):
        return {
            "price": 0.0, "balance": 100.0, "total_trades": 0, "win_rate": 0,
            "total_pnl": 0.0, "last_update": "Loading...", "in_position": False,
            "analysis_1m": {"rsi": 0, "signal": "WAIT", "pats": []},
            "analysis_3m": {"rsi": 0, "macd": 0, "signal": "WAIT", "pats": []},
            "wait_reason": "বিশ্লেষণ চলছে...", "log": []
        }
    with open(STATE_FILE, "r") as f:
        return json.load(f)

app = Flask(__name__)

# --- অ্যাডভান্সড ১২-প্যাটার্ন ডিটেক্টর ---
def detect_advanced_patterns(df):
    pats = []
    if len(df) < 5: return pats
    c1, c2, c3 = df.iloc[-1], df.iloc[-2], df.iloc[-3]
    
    def parts(c):
        body = abs(c['c'] - c['o'])
        total = max(0.001, c['h'] - c['l'])
        u_wick = c['h'] - max(c['c'], c['o'])
        l_wick = min(c['c'], c['o']) - c['l']
        return body, total, u_wick, l_wick

    b1, t1, u1, l1 = parts(c1)
    b2, t2, u2, l2 = parts(c2)

    # বুলিশ প্যাটার্নস
    if b1 > 0 and l1 >= 2 * b1 and u1 <= 0.2 * b1: pats.append({"n": "Hammer &#128296;", "t": "bull"})
    if b1 > 0 and u1 >= 2 * b1 and l1 <= 0.2 * b1: pats.append({"n": "Inv. Hammer &#128296;", "t": "bull"})
    if c2['c'] < c2['o'] and c1['c'] > c1['o'] and c1['c'] >= c2['o'] and c1['o'] <= c2['c']: pats.append({"n": "Bullish Engulfing &#128200;", "t": "bull"})
    if b1 / t1 > 0.85 and c1['c'] > c1['o']: pats.append({"n": "Bull Marubozu &#128170;", "t": "bull"})
    if c3['c'] < c3['o'] and abs(c2['c']-c2['o']) < (abs(c3['c']-c3['o'])*0.3) and c1['c'] > (c3['o']+c3['c'])/2: pats.append({"n": "Morning Star &#127749;", "t": "bull"})

    # বেয়ারিশ প্যাটার্নস
    if b1 > 0 and u1 >= 2 * b1 and l1 <= 0.2 * b1: pats.append({"n": "Shooting Star &#9732;", "t": "bear"})
    if c2['c'] > c2['o'] and c1['c'] < c1['o'] and c1['c'] <= c2['o'] and c1['o'] >= c2['c']: pats.append({"n": "Bearish Engulfing &#128201;", "t": "bear"})
    if b1 / t1 > 0.85 and c1['c'] < c1['o']: pats.append({"n": "Bear Marubozu &#128308;", "t": "bear"})

    # নিরপেক্ষ
    if b1 <= (t1 * 0.1): pats.append({"n": "Doji &#9878;", "t": "neut"})
    return pats

# --- ট্রেডিং ইঞ্জিন ---
def bot_engine():
    in_pos, holdings, entry_p = False, 0.0, 0.0
    wins, total, net_pnl = 0, 0, 0.0
    while True:
        try:
            bars1 = exchange.fetch_ohlcv(SYMBOL, '1m', limit=100)
            bars3 = exchange.fetch_ohlcv(SYMBOL, '3m', limit=100)
            df1 = pd.DataFrame(bars1, columns=['t','o','h','l','c','v'])
            df3 = pd.DataFrame(bars3, columns=['t','o','h','l','c','v'])
            p = df1['c'].iloc[-1]
            r1 = ta.momentum.rsi(df1['c']).fillna(0).iloc[-1]
            e20 = ta.trend.ema_indicator(df1['c'], window=20).fillna(0).iloc[-1]
            m = ta.trend.macd(df3['c']).fillna(0).iloc[-1]
            ms = ta.trend.macd_signal(df3['c']).fillna(0).iloc[-1]

            cur = load_state()
            live_pnl = ((p / entry_p) - 1) * 100 if in_pos else 0.0

            cur.update({
                "price": round(p, 2), "last_update": datetime.now(timezone.utc).strftime("%H:%M:%S"),
                "in_position": in_pos, "live_pnl": round(live_pnl, 2),
                "analysis_1m": {"rsi": round(r1,1), "signal": "BULL" if p > e20 else "BEAR", "pats": detect_advanced_patterns(df1)},
                "analysis_3m": {"rsi": round(ta.momentum.rsi(df3['c']).fillna(0).iloc[-1],1), "macd": round(m,3), "signal": "BULL" if m > ms else "WAIT", "pats": detect_advanced_patterns(df3)},
                "wait_reason": "এন্ট্রি খুঁজছে..." if not in_pos else "ট্রেড লাইভ আছে"
            })

            if not in_pos and p > e20 and r1 < 65 and m > ms:
                holdings = cur["balance"] / p
                cur["balance"], entry_p, in_pos = 0.0, p, True
                total += 1
                cur["total_trades"] = total
                cur["log"].insert(0, {"t": datetime.now().strftime("%H:%M"), "m": "🟢 BUY @ $" + str(round(p,2))})
            elif in_pos:
                if live_pnl >= 0.7 or live_pnl <= -1.0:
                    cur["balance"] = holdings * p
                    in_pos = False
                    trade_pnl = cur["balance"] - 100.0
                    if live_pnl > 0: wins += 1
                    cur.update({"total_pnl": round(trade_pnl, 2), "win_rate": round((wins/total)*100, 1)})
                    cur["log"].insert(0, {"t": datetime.now().strftime("%H:%M"), "m": f"🔴 SELL @ ${round(p,2)}"})
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
    <title>Pro AI Max</title><script src="https://cdn.tailwindcss.com"></script>
    <style>
        @import url('https://fonts.googleapis.com/css2?family=Plus+Jakarta+Sans:wght@700;800&display=swap');
        body { font-family: 'Plus Jakarta Sans', sans-serif; background-color: #f8fafc; }
        .big-card { background: white; border-radius: 2.5rem; box-shadow: 0 25px 50px -12px rgba(0,0,0,0.1); border: 1px solid #f1f5f9; }
        .p-tag { padding: 8px 20px; border-radius: 99px; font-size: 14px; font-weight: 800; display: inline-block; margin: 5px; border: 1px solid rgba(0,0,0,0.05); }
        .tag-bull { background: #dcfce7; color: #166534; }
        .tag-bear { background: #fee2e2; color: #991b1b; }
        .tag-neut { background: #f1f5f9; color: #475569; }
    </style>
</head>
<body class="p-4 md:p-12">
<div class="max-w-3xl mx-auto">
    <div class="text-center mb-10">
        <h1 class="text-5xl font-extrabold text-slate-900 mb-2 uppercase">&#129302; SOL PRO SYSTEM</h1>
        <p class="text-emerald-500 font-bold tracking-[0.3em] text-sm uppercase">24/7 Live Scalping</p>
    </div>

    <div class="grid grid-cols-3 gap-5 mb-10 text-center">
        <div class="big-card p-8"><p class="text-xs font-bold text-slate-400 mb-1 uppercase">Trades</p><p id="t" class="text-4xl font-black">0</p></div>
        <div class="big-card p-8"><p class="text-xs font-bold text-slate-400 mb-1 uppercase">Win Rate</p><p id="w" class="text-4xl font-black text-emerald-500">0%</p></div>
        <div class="big-card p-8"><p class="text-xs font-bold text-slate-400 mb-1 uppercase">Net P&L</p><p id="pnl" class="text-4xl font-black text-blue-600">$0.00</p></div>
    </div>

    <div class="big-card p-12 mb-10 text-center border-t-8 border-t-slate-900">
        <p class="text-sm font-bold text-slate-400 uppercase tracking-widest mb-4">Solana Live Market Price</p>
        <h2 id="pr" class="text-[10rem] leading-none font-black tracking-tighter text-slate-900 mb-10">$0.00</h2>
        
        <div id="pnl_display" class="hidden mb-10 p-10 bg-slate-900 rounded-[3rem] text-white shadow-2xl">
            <p class="text-sm font-bold text-slate-500 uppercase mb-2">Live Trade Position</p>
            <p id="live_pnl" class="text-7xl font-black">0.00%</p>
        </div>

        <div id="st" class="bg-amber-100 text-amber-700 py-5 rounded-[2rem] text-base font-black uppercase tracking-widest border-2 border-amber-200">
            &#8987; ANALYZING MARKET DATA...
        </div>
    </div>

    <div class="grid md:grid-cols-2 gap-8 mb-10">
        <div class="big-card p-10">
            <div class="flex justify-between items-center mb-8"><h3 class="font-black text-2xl text-slate-800 uppercase italic">1M Scan</h3><span id="s1" class="text-xs font-black px-8 py-3 rounded-full border-2">WAIT</span></div>
            <div class="flex justify-between text-xl font-bold text-slate-600 px-2"><span>RSI</span><b id="r1" class="text-slate-900 text-3xl">0</b></div>
            <div id="p1" class="mt-8 flex flex-wrap"></div>
        </div>
        <div class="big-card p-10">
            <div class="flex justify-between items-center mb-8"><h3 class="font-black text-2xl text-slate-800 uppercase italic">3M Scan</h3><span id="s3" class="text-xs font-black px-8 py-3 rounded-full border-2">WAIT</span></div>
            <div class="flex justify-between text-xl font-bold text-slate-600 px-2"><span>MACD</span><b id="m3" class="text-slate-900 text-3xl">0</b></div>
            <div id="p3" class="mt-8 flex flex-wrap"></div>
        </div>
    </div>

    <div class="big-card overflow-hidden h-[500px] mb-12 border-[15px] border-white shadow-2xl">
        <iframe src="https://s.tradingview.com/widgetembed/?symbol=BITGET%3ASOLUSDT&interval=1&theme=light" width="100%" height="100%" frameborder="0"></iframe>
    </div>

    <div class="big-card p-10 mb-10">
        <h3 class="font-black text-base text-slate-400 uppercase mb-8 tracking-widest">&#128203; RECENT LOGS</h3>
        <div id="lg" class="space-y-6"></div>
    </div>
    
    <p class="text-center text-xs font-bold text-slate-300 pb-20 uppercase tracking-[0.5em]">System Sync: <span id="ck">--:--:--</span></p>
</div>

<script>
    async function update() {
        try {
            const r = await fetch('/api/data'); const d = await r.json();
            if(d.price > 0) {
                document.getElementById('pr').innerText = '$' + d.price;
                document.getElementById('t').innerText = d.total_trades;
                document.getElementById('w').innerText = d.win_rate + '%';
                document.getElementById('pnl').innerText = '$' + d.total_pnl.toFixed(2);
                document.getElementById('st').innerText = d.wait_reason;
                document.getElementById('ck').innerText = d.last_update;
                
                if(d.in_position) {
                    document.getElementById('pnl_display').classList.remove('hidden');
                    const lp = document.getElementById('live_pnl');
                    lp.innerText = (d.live_pnl >= 0 ? '+' : '') + d.live_pnl + '%';
                    lp.className = d.live_pnl >= 0 ? 'text-7xl font-black text-emerald-400' : 'text-7xl font-black text-rose-400';
                } else { document.getElementById('pnl_display').classList.add('hidden'); }

                document.getElementById('r1').innerText = d.analysis_1m.rsi;
                document.getElementById('s1').innerText = d.analysis_1m.signal;
                document.getElementById('s1').className = d.analysis_1m.signal === 'BULL' ? 'text-xs font-black px-8 py-3 rounded-full bg-emerald-500 text-white' : 'text-xs font-black px-8 py-3 rounded-full bg-rose-500 text-white';
                
                document.getElementById('m3').innerText = d.analysis_3m.macd;
                document.getElementById('s3').innerText = d.analysis_3m.signal;
                document.getElementById('s3').className = d.analysis_3m.signal === 'BULL' ? 'text-xs font-black px-8 py-3 rounded-full bg-blue-500 text-white' : 'text-xs font-black px-8 py-3 rounded-full bg-slate-300 text-white';
                
                document.getElementById('p1').innerHTML = d.analysis_1m.pats.map(p => `<span class="p-tag ${p.t === 'bull' ? 'tag-bull' : (p.t === 'bear' ? 'tag-bear' : 'tag-neut')}">${p.n}</span>`).join('');
                document.getElementById('p3').innerHTML = d.analysis_3m.pats.map(p => `<span class="p-tag ${p.t === 'bull' ? 'tag-bull' : (p.t === 'bear' ? 'tag-bear' : 'tag-neut')}">${p.n}</span>`).join('');
                document.getElementById('lg').innerHTML = d.log.slice(0,5).map(l => `<div class="flex justify-between items-center bg-slate-50 p-6 rounded-[2rem] font-bold text-lg"><span>${l.t}</span><span class="text-slate-800">${l.m}</span></div>`).join('');
            }
        } catch(e) {}
    }
    setInterval(update, 3000); update();
</script>
</body></html>
"""

if __name__ == "__main__":
    app.run(host='0.0.0.0', port=int(os.environ.get('PORT', 10000)))
