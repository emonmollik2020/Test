import ccxt
import pandas as pd
import ta
import time
import threading
import json
import os
from flask import Flask, render_template_string, jsonify
from datetime import datetime, timezone

# ========== 1. কনফিগারেশন ==========
SYMBOL = "SOL/USDT"
STATE_FILE = "bot_state.json"
INITIAL_FUND = 100.0
TP_PCT = 0.007    # 0.7% লাভ
SL_PCT = 0.010    # 1.0% লস
exchange = ccxt.bitget({'enableRateLimit': True})

def save_state(data):
    with open(STATE_FILE, "w") as f:
        json.dump(data, f)

def load_state():
    if not os.path.exists(STATE_FILE):
        return {
            "price": 0.0, "balance": 100.0, "pnl": 0.0, "last_update": "...",
            "total_trades": 0, "win_rate": 0.0, "best": 0.0, "worst": 0.0,
            "in_position": False, "live_pnl": 0.0, "entry_price": 0.0,
            "analysis_1m": {"rsi": 0, "ema20": 0, "signal": "WAIT", "pats": []},
            "analysis_3m": {"rsi": 0, "macd": 0, "stoch": 0, "signal": "WAIT", "pats": []},
            "wait_reason": "Loading...", "log": []
        }
    with open(STATE_FILE, "r") as f:
        return json.load(f)

app = Flask(__name__)

# --- অ্যাডভান্সড প্যাটার্ন ডিটেক্টর ---
def detect_pats(df):
    pats = []
    if len(df) < 5: return pats
    o, h, l, c = df['o'].iloc[-1], df['h'].iloc[-1], df['l'].iloc[-1], df['c'].iloc[-1]
    po, pc = df['o'].iloc[-2], df['c'].iloc[-2]
    body = abs(c - o)
    full = h - l if h != l else 0.001
    
    if body > 0 and (min(c, o) - l) >= 1.8 * body: pats.append({"n": "Hammer &#128296;", "t": "bull"})
    if pc < po and c > o and c >= po: pats.append({"n": "Bull Engulfing &#128200;", "t": "bull"})
    if pc > po and c < o and c <= po: pats.append({"n": "Bear Engulfing &#128201;", "t": "bear"})
    if body <= (full * 0.1): pats.append({"n": "Doji &#9878;", "t": "neut"})
    return pats

# --- ট্রেডিং ইঞ্জিন ---
def bot_engine():
    pnl_hist = []
    in_pos, holdings, entry_p = False, 0.0, 0.0
    wins, total = 0, 0

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
            sk = ta.momentum.stochrsi_k(df3['c']).fillna(0).iloc[-1] * 100

            cur = load_state()
            live_pnl = ((p / entry_p) - 1) * 100 if in_pos else 0.0

            cur.update({
                "price": round(p, 2), "last_update": datetime.now(timezone.utc).strftime("%H:%M:%S"),
                "in_position": in_pos, "live_pnl": round(live_pnl, 2), "entry_price": round(entry_p, 2),
                "analysis_1m": {"rsi": round(r1,1), "ema20": round(e20,2), "signal": "BULL" if p > e20 else "BEAR", "pats": detect_pats(df1)},
                "analysis_3m": {"rsi": round(ta.momentum.rsi(df3['c']).fillna(0).iloc[-1],1), "macd": round(m,3), "stoch": round(sk,1), "signal": "BULL" if m > ms else "WAIT", "pats": detect_pats(df3)},
                "wait_reason": "এন্ট্রি খুঁজছে..." if not in_pos else "ট্রেড লাইভ আছে"
            })

            # BUY
            if not in_pos and p > e20 and r1 < 65 and m > ms:
                holdings = cur["balance"] / p
                cur["balance"], entry_p, in_pos = 0.0, p, True
                total += 1
                cur["total_trades"] = total
                cur["log"].insert(0, {"t": datetime.now().strftime("%H:%M"), "m": "🟢 BUY @ $" + str(round(p,2))})
            
            # SELL
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

# --- আধুনিক এবং মার্জিত ড্যাশবোর্ড ডিজাইন ---
UI = """
<!DOCTYPE html>
<html lang="bn">
<head>
    <meta charset="UTF-8"><meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>SOL Pro Dashboard</title><script src="https://cdn.tailwindcss.com"></script>
    <style>
        @import url('https://fonts.googleapis.com/css2?family=Plus+Jakarta+Sans:wght@400;600;800&display=swap');
        body { font-family: 'Plus Jakarta Sans', sans-serif; background-color: #f8fafc; color: #334155; }
        .card { background: white; border-radius: 1.25rem; box-shadow: 0 4px 6px -1px rgba(0,0,0,0.05), 0 2px 4px -1px rgba(0,0,0,0.03); border: 1px solid #f1f5f9; }
        .tag-bull { background: #dcfce7; color: #166534; }
        .tag-bear { background: #fee2e2; color: #991b1b; }
        .tag-neut { background: #f1f5f9; color: #475569; }
        .p-tag { padding: 4px 12px; border-radius: 99px; font-size: 11px; font-weight: 700; display: inline-block; margin: 2px; border: 1px solid rgba(0,0,0,0.03); }
    </style>
</head>
<body class="p-4">
<div class="max-w-md mx-auto">
    <div class="text-center mb-6">
        <h1 class="text-xl font-extrabold text-slate-800 tracking-tight">&#129302; SOL PRO SYSTEM</h1>
        <div class="flex items-center justify-center gap-2 mt-1">
            <div class="w-2 h-2 bg-emerald-500 rounded-full animate-pulse"></div>
            <span class="text-[9px] font-bold text-slate-400 uppercase tracking-widest">Live 24/7 Analysis</span>
        </div>
    </div>

    <!-- Stats Row -->
    <div class="grid grid-cols-3 gap-3 mb-4">
        <div class="card p-3 text-center"><p class="text-[8px] font-bold text-slate-400 uppercase">Trades</p><p id="t" class="text-lg font-extrabold">0</p></div>
        <div class="card p-3 text-center"><p class="text-[8px] font-bold text-slate-400 uppercase">Win Rate</p><p id="w" class="text-lg font-extrabold text-emerald-500">0%</p></div>
        <div class="card p-3 text-center"><p class="text-[8px] font-bold text-slate-400 uppercase">Net P&L</p><p id="pnl" class="text-lg font-extrabold text-blue-600">$0.00</p></div>
    </div>

    <!-- Main Section -->
    <div class="card p-6 mb-4 text-center">
        <div class="flex justify-between items-center mb-4">
            <h2 id="pr" class="text-5xl font-extrabold tracking-tight text-slate-900">$0.00</h2>
            <div class="text-right"><p class="text-[9px] font-bold text-slate-400">Balance: <span id="bl" class="text-slate-800">$100.00</span></p></div>
        </div>
        
        <div id="pnl_display" class="hidden mb-4 p-4 bg-slate-900 rounded-2xl text-white">
            <p class="text-[8px] font-bold text-slate-400 uppercase mb-1">Position Profit</p>
            <p id="live_pnl" class="text-3xl font-extrabold">0.00%</p>
        </div>

        <div id="st" class="bg-orange-50 text-orange-600 py-2.5 rounded-xl text-[10px] font-bold border border-orange-100 uppercase tracking-wide">
            &#8987; লোড হচ্ছে...
        </div>
    </div>

    <!-- Analysis -->
    <div class="grid grid-cols-2 gap-3 mb-4">
        <div class="card p-4">
            <div class="flex justify-between items-center mb-3"><h3 class="font-extrabold text-[10px] text-slate-400 uppercase">1m Scan</h3><span id="s1" class="text-[8px] font-bold px-2 py-0.5 rounded-full border">WAIT</span></div>
            <p class="text-[11px] font-bold">RSI: <b id="r1" class="text-slate-900">0</b></p>
            <div id="p1" class="mt-2 flex flex-wrap"></div>
        </div>
        <div class="card p-4">
            <div class="flex justify-between items-center mb-3"><h3 class="font-extrabold text-[10px] text-slate-400 uppercase">3m Scan</h3><span id="s3" class="text-[8px] font-bold px-2 py-0.5 rounded-full border">WAIT</span></div>
            <p class="text-[11px] font-bold">MACD: <b id="m3" class="text-slate-900">0</b></p>
            <div id="p3" class="mt-2 flex flex-wrap"></div>
        </div>
    </div>

    <!-- Small Chart -->
    <div class="card overflow-hidden h-48 mb-4">
        <iframe src="https://s.tradingview.com/widgetembed/?symbol=BITGET%3ASOLUSDT&interval=1&theme=light" width="100%" height="100%" frameborder="0"></iframe>
    </div>

    <!-- Logs -->
    <div class="card p-4 mb-6">
        <h3 class="text-[9px] font-bold text-slate-300 uppercase tracking-widest mb-3">&#128203; Live Log</h3>
        <div id="lg" class="space-y-2"></div>
    </div>
    
    <p class="text-center text-[8px] font-bold text-slate-300 uppercase pb-6">Sync Time: <span id="ck">--:--:--</span></p>
</div>

<script>
    async function update() {
        try {
            const r = await fetch('/api/data'); const d = await r.json();
            if(d.price > 0) {
                document.getElementById('pr').innerText = '$' + d.price;
                document.getElementById('bl').innerText = '$' + d.balance.toFixed(2);
                document.getElementById('t').innerText = d.total_trades;
                document.getElementById('w').innerText = d.win_rate + '%';
                document.getElementById('pnl').innerText = '$' + d.total_pnl.toFixed(2);
                document.getElementById('st').innerText = d.wait_reason;
                document.getElementById('ck').innerText = d.last_update;
                
                if(d.in_position) {
                    document.getElementById('pnl_display').classList.remove('hidden');
                    const lp = document.getElementById('live_pnl');
                    lp.innerText = (d.live_pnl >= 0 ? '+' : '') + d.live_pnl + '%';
                    lp.className = d.live_pnl >= 0 ? 'text-3xl font-extrabold text-emerald-400' : 'text-3xl font-extrabold text-rose-400';
                } else { document.getElementById('pnl_display').classList.add('hidden'); }

                document.getElementById('r1').innerText = d.analysis_1m.rsi;
                document.getElementById('s1').innerText = d.analysis_1m.signal;
                document.getElementById('s1').className = d.analysis_1m.signal === 'BULL' ? 'text-[8px] font-bold px-2 py-0.5 rounded-full bg-emerald-100 text-emerald-600' : 'text-[8px] font-bold px-2 py-0.5 rounded-full bg-rose-50 text-rose-500';
                
                document.getElementById('m3').innerText = d.analysis_3m.macd;
                document.getElementById('s3').innerText = d.analysis_3m.signal;
                document.getElementById('s3').className = d.analysis_3m.signal === 'BULL' ? 'text-[8px] font-bold px-2 py-0.5 rounded-full bg-blue-100 text-blue-600' : 'text-[8px] font-bold px-2 py-0.5 rounded-full bg-slate-50 text-slate-400';
                
                document.getElementById('p1').innerHTML = d.analysis_1m.pats.map(p => `<span class="p-tag ${p.t === 'bull' ? 'tag-bull' : (p.t === 'bear' ? 'tag-bear' : 'tag-neut')}">${p.n}</span>`).join('');
                document.getElementById('p3').innerHTML = d.analysis_3m.pats.map(p => `<span class="p-tag ${p.t === 'bull' ? 'tag-bull' : (p.t === 'bear' ? 'tag-bear' : 'tag-neut')}">${p.n}</span>`).join('');
                document.getElementById('lg').innerHTML = d.log.slice(0,3).map(l => `<div class="flex justify-between font-bold text-[9px]"><span class="text-slate-300">${l.t}</span><span class="text-slate-500">${l.m}</span></div>`).join('');
            }
        } catch(e) {}
    }
    setInterval(update, 4000); update();
</script>
</body></html>
