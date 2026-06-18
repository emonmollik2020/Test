import ccxt
import pandas as pd
import ta
import time
import threading
import json
import os
from flask import Flask, render_template_string, jsonify
from datetime import datetime, timezone

# ========== ১. কনফিগারেশন ও ডাটা পাথ ==========
SYMBOL = "SOL/USDT"
STATE_FILE = "bot_state.json"
INITIAL_FUND = 100.0
exchange = ccxt.bitget({'enableRateLimit': True})

def save_state(data):
    with open(STATE_FILE, "w") as f:
        json.dump(data, f)

def load_state():
    if not os.path.exists(STATE_FILE):
        return {
            "price": 0.0, "balance": 100.0, "pnl": 0.0, "last_update": "Syncing...",
            "total_trades": 0, "win_rate": 0.0, "best": 0.0, "worst": 0.0, "last_action": "---",
            "in_position": False, "live_pnl": 0.0, "entry_price": 0.0,
            "analysis_1m": {"rsi": 0, "ema20": 0, "signal": "WAIT", "pats": []},
            "analysis_3m": {"rsi": 0, "macd": 0, "stoch": 0, "signal": "WAIT", "pats": []},
            "wait_reason": "এনালাইজার শুরু হচ্ছে...", "log": []
        }
    with open(STATE_FILE, "r") as f:
        try: return json.load(f)
        except: return load_state()

app = Flask(__name__)

# --- অ্যাডভান্সড ১২-প্যাটার্ন ইঞ্জিন ---
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
    if b1 > 0 and l1 >= 2 * b1 and u1 <= 0.2 * b1: pats.append({"n": "Hammer &#128296;", "t": "bull"})
    if c2['c'] < c2['o'] and c1['c'] > c1['o'] and c1['c'] >= c2['o'] and c1['o'] <= c2['c']: pats.append({"n": "Bull Engulfing &#128200;", "t": "bull"})
    if b1 / t1 > 0.85 and c1['c'] > c1['o']: pats.append({"n": "Bull Marubozu &#128170;", "t": "bull"})
    if b1 > 0 and u1 >= 2 * b1 and l1 <= 0.2 * b1: pats.append({"n": "Shooting Star &#9732;", "t": "bear"})
    if c2['c'] > c2['o'] and c1['c'] < c1['o'] and c1['c'] <= c2['o'] and c1['o'] >= c2['c']: pats.append({"n": "Bear Engulfing &#128201;", "t": "bear"})
    if b1 <= (t1 * 0.1): pats.append({"n": "Doji &#9878;", "t": "neut"})
    return pats

# --- ট্রেডিং ইঞ্জিন ---
def bot_engine():
    pnl_hist = []
    in_pos, holdings, entry_p = False, 0.0, 0.0
    wins, total = 0, 0
    while True:
        try:
            bars1 = exchange.fetch_ohlcv(SYMBOL, '1m', limit=150)
            bars3 = exchange.fetch_ohlcv(SYMBOL, '3m', limit=150)
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
                "in_position": in_pos, "live_pnl": round(live_pnl, 2), "entry_price": round(entry_p, 2),
                "analysis_1m": {"rsi": round(r1,1), "ema20": round(e20,2), "signal": "BULL" if p > e20 else "BEAR", "pats": detect_advanced_patterns(df1)},
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
                    p_val = cur["balance"] - 100.0
                    pnl_hist.append(p_val)
                    if live_pnl > 0: wins += 1
                    cur.update({"total_pnl": round(p_val, 2), "win_rate": round((wins/total)*100, 1), "best": round(max(pnl_hist), 2), "last_action": "SELL"})
                    cur["log"].insert(0, {"t": datetime.now().strftime("%H:%M"), "m": f"🔴 SELL @ ${round(p,2)} ({round(live_pnl,1)}%)"})
            save_state(cur)
        except: pass
        time.sleep(10)

threading.Thread(target=bot_engine, daemon=True).start()

@app.route('/api/data')
def api(): return jsonify(load_state())

@app.route('/')
def index(): return render_template_string(UI)

# --- আপনার সেই বিশাল ও প্রফেশনাল ডিজাইন (ফ্লিক্সড পিএন্ডএল বক্স) ---
UI = """
<!DOCTYPE html>
<html>
<head>
    <meta charset="UTF-8"><meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Pro Bot Max</title><script src="https://cdn.tailwindcss.com"></script>
    <style>
        @import url('https://fonts.googleapis.com/css2?family=Plus+Jakarta+Sans:wght@400;700;800&display=swap');
        body { font-family: 'Plus Jakarta Sans', sans-serif; background-color: #f1f5f9; }
        .big-card { background: white; border-radius: 2.5rem; box-shadow: 0 25px 50px -12px rgba(0,0,0,0.1); border: 1px solid white; }
        .p-tag { padding: 8px 18px; border-radius: 99px; font-size: 14px; font-weight: 800; display: inline-block; margin: 4px; border: 1px solid rgba(0,0,0,0.05); }
        .tag-bull { background: #dcfce7; color: #166534; }
        .tag-bear { background: #fee2e2; color: #991b1b; }
        .tag-neut { background: #f1f5f9; color: #475569; }
    </style>
</head>
<body class="p-4 md:p-10">
<div class="max-w-2xl mx-auto">
    <div class="text-center mb-10"><h1 class="text-4xl font-extrabold text-slate-900 mb-2">&#129302; SOL PRO SYSTEM</h1><p class="text-emerald-500 font-bold tracking-widest text-sm uppercase">24/7 Live Scalping</p></div>

    <div class="grid grid-cols-3 gap-4 mb-8">
        <div class="big-card p-6 text-center shadow-sm"><p class="text-[10px] font-bold text-slate-400 mb-1 uppercase">Trades</p><p id="t" class="text-3xl font-black text-slate-800">0</p></div>
        <div class="big-card p-6 text-center shadow-sm"><p class="text-[10px] font-bold text-slate-400 mb-1 uppercase">Wins</p><p id="w" class="text-4xl font-black text-emerald-500">0%</p></div>
        <div class="big-card p-6 text-center shadow-sm"><p class="text-[10px] font-bold text-slate-400 mb-1 uppercase">Profit</p><p id="pnl" class="text-3xl font-black text-blue-600">$0.00</p></div>
    </div>

    <div class="big-card p-12 mb-8 text-center border-t-8 border-t-slate-800">
        <p class="text-sm font-bold text-slate-400 uppercase tracking-widest mb-2">Solana Live Price</p>
        <h2 id="pr" class="text-9xl font-black tracking-tighter text-slate-900 mb-8">$0.00</h2>
        
        <!-- P&L Box Fixed: Now White Background with Border instead of Black -->
        <div id="pnl_display" class="hidden mb-8 p-8 border-4 border-slate-100 rounded-[3rem] bg-white shadow-lg">
            <p class="text-xs font-bold text-slate-400 uppercase mb-2 tracking-widest">Live Trade Position</p>
            <p id="live_pnl" class="text-7xl font-black">0.00%</p>
        </div>

        <div id="st" class="bg-amber-100 text-amber-700 py-4 rounded-3xl text-sm font-black uppercase tracking-widest border border-amber-200">⌛ লোড হচ্ছে...</div>
    </div>

    <div class="space-y-6 mb-10 text-center">
        <div class="big-card p-8"><div class="flex justify-between items-center mb-6"><h3 class="font-black text-xl text-slate-800 uppercase italic">1M Scan</h3><span id="s1" class="text-xs font-black px-6 py-2 rounded-full border">WAIT</span></div><div class="flex justify-around text-lg font-bold text-slate-500"><span>RSI: <b id="r1" class="text-slate-900">0</b></span><span>E20: <b id="e1" class="text-slate-900">$0</b></span></div><div id="p1" class="mt-6 flex justify-center flex-wrap"></div></div>
        <div class="big-card p-8"><div class="flex justify-between items-center mb-6"><h3 class="font-black text-xl text-slate-800 uppercase italic">3M Scan</h3><span id="s3" class="text-xs font-black px-6 py-2 rounded-full border">WAIT</span></div><div class="flex justify-around text-lg font-bold text-slate-500"><span>RSI: <b id="r3" class="text-slate-900">0</b></span><span>MACD: <b id="m3" class="text-slate-900">0</b></span></div><div id="p3" class="mt-6 flex justify-center flex-wrap"></div></div>
    </div>

    <div class="big-card overflow-hidden h-[450px] mb-10 border-[12px] border-white shadow-2xl"><iframe src="https://s.tradingview.com/widgetembed/?symbol=BITGET%3ASOLUSDT&interval=1&theme=light" width="100%" height="100%" frameborder="0"></iframe></div>
    <div class="big-card p-8 mb-10"><h3 class="font-black text-sm text-slate-400 uppercase mb-6 tracking-widest">&#128203; RECENT LOGS</h3><div id="lg" class="space-y-4"></div></div>
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
                if(d.in_position) {
                    document.getElementById('pnl_display').classList.remove('hidden');
                    const lp = document.getElementById('live_pnl');
                    lp.innerText = (d.live_pnl >= 0 ? '+' : '') + d.live_pnl + '%';
                    lp.className = d.live_pnl >= 0 ? 'text-7xl font-black text-emerald-500' : 'text-7xl font-black text-rose-500';
                } else { document.getElementById('pnl_display').classList.add('hidden'); }
                document.getElementById('r1').innerText = d.analysis_1m.rsi;
                document.getElementById('e1').innerText = '$' + d.analysis_1m.ema20;
                document.getElementById('s1').innerText = d.analysis_1m.signal;
                document.getElementById('s1').className = d.analysis_1m.signal === 'BULL' ? 'text-xs font-black px-6 py-2 rounded-full bg-emerald-50 text-emerald-600' : 'text-xs font-black px-6 py-2 rounded-full bg-rose-50 text-rose-500';
                document.getElementById('r3').innerText = d.analysis_3m.rsi;
                document.getElementById('m3').innerText = d.analysis_3m.macd;
                document.getElementById('s3').innerText = d.analysis_3m.signal;
                document.getElementById('s3').className = d.analysis_3m.signal === 'BULL' ? 'text-xs font-black px-6 py-2 rounded-full bg-blue-50 text-blue-600' : 'text-xs font-black px-6 py-2 rounded-full bg-slate-100 text-slate-400';
                document.getElementById('p1').innerHTML = d.analysis_1m.pats.map(p => `<span class="p-tag ${p.t === 'bull' ? 'tag-bull' : (p.t === 'bear' ? 'tag-bear' : 'tag-neut')}">${p.n}</span>`).join('');
                document.getElementById('p3').innerHTML = d.analysis_3m.pats.map(p => `<span class="p-tag ${p.t === 'bull' ? 'tag-bull' : (p.t === 'bear' ? 'tag-bear' : 'tag-neut')}">${p.n}</span>`).join('');
                document.getElementById('lg').innerHTML = d.log.slice(0,5).map(l => `<div class="flex justify-between items-center bg-slate-50 p-5 rounded-3xl font-bold text-base"><span>${l.t}</span><span class="text-slate-800">${l.m}</span></div>`).join('');
            }
        } catch(e) {}
    }
    setInterval(update, 3000); update();
</script>
</body></html>
"""

if __name__ == "__main__":
    app.run(host='0.0.0.0', port=int(os.environ.get('PORT', 8080)))
