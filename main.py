import ccxt
import pandas as pd
import ta
import time
import threading
import json
import os
from flask import Flask, render_template_string, jsonify
from datetime import datetime, timezone

# ========== ১. সেটিংস ও ফাইল পাথ ==========
SYMBOL = "SOL/USDT"
STATE_FILE = "bot_state.json"
exchange = ccxt.bitget()

# প্রাথমিক ডাটা সেটআপ
def save_state(data):
    with open(STATE_FILE, "w") as f:
        json.dump(data, f)

def load_state():
    if not os.path.exists(STATE_FILE):
        return {"price": 0.0, "trades": 0, "win": "0%", "pnl": "0.00", "r1": 0, "r3": 0, "s1": "WAIT", "s3": "WAIT", "upd": "Loading...", "pats": []}
    with open(STATE_FILE, "r") as f:
        return json.load(f)

save_state(load_state()) # ফাইলটি তৈরি করে নেওয়া

app = Flask(__name__)

# --- প্যাটার্ন ডিটেকশন ---
def get_pats(df):
    p = []
    if len(df) < 3: return p
    o, c = df['o'].iloc[-1], df['c'].iloc[-1]
    po, pc = df['o'].iloc[-2], df['c'].iloc[-2]
    if pc < po and c > o and c >= po: p.append("Bullish Engulfing")
    if (min(c,o)-df['l'].iloc[-1]) >= 1.8*abs(c-o): p.append("Hammer")
    return p

# --- বটের ইঞ্জিন (ডাটা ফাইলে লিখবে) ---
def bot_engine():
    total_trades, wins, net_pnl = 0, 0, 0.0
    while True:
        try:
            bars1 = exchange.fetch_ohlcv(SYMBOL, '1m', limit=50)
            bars3 = exchange.fetch_ohlcv(SYMBOL, '3m', limit=50)
            df1 = pd.DataFrame(bars1, columns=['t','o','h','l','c','v'])
            df3 = pd.DataFrame(bars3, columns=['t','o','h','l','c','v'])
            
            p = df1['c'].iloc[-1]
            r1 = ta.momentum.rsi(df1['c']).fillna(0).iloc[-1]
            r3 = ta.momentum.rsi(df3['c']).fillna(0).iloc[-1]
            e20 = ta.trend.ema_indicator(df1['c'], window=20).fillna(0).iloc[-1]

            # নতুন ডাটা ফাইলে সেভ করা
            new_data = {
                "price": round(p, 2),
                "trades": total_trades,
                "win": "0%",
                "pnl": f"${round(net_pnl, 2)}",
                "r1": round(r1, 1),
                "r3": round(r3, 1),
                "s1": "BULLISH" if p > e20 else "BEARISH",
                "s3": "WAIT",
                "upd": datetime.now(timezone.utc).strftime("%H:%M:%S"),
                "pats": get_pats(df1)
            }
            save_state(new_data)
        except: pass
        time.sleep(10)

# ইঞ্জিন চালু
threading.Thread(target=bot_engine, daemon=True).start()

@app.route('/api/data')
def api(): return jsonify(load_state())

@app.route('/')
def index(): return render_template_string(UI)

# --- ড্যাশবোর্ড ডিজাইন (আপনার চাহিদা অনুযায়ী) ---
UI = """
<!DOCTYPE html>
<html>
<head>
    <meta charset="UTF-8"><meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Live SOL Bot</title><script src="https://cdn.tailwindcss.com"></script>
</head>
<body class="bg-[#f8fafc] p-4 font-sans text-slate-800">
<div class="max-w-md mx-auto">
    <div class="text-center mb-6">
        <h1 class="text-2xl font-black text-slate-900 uppercase">SOL/USDT Trading Bot</h1>
        <p class="text-green-500 text-[10px] font-bold tracking-widest mt-1">CONNECTED TO CLOUD</p>
    </div>

    <div class="grid grid-cols-3 gap-3 mb-6 text-center text-xs">
        <div class="bg-white p-3 rounded-2xl shadow-sm"><p class="font-bold text-slate-400">TRADES</p><p id="t" class="text-lg font-black">0</p></div>
        <div class="bg-white p-3 rounded-2xl shadow-sm"><p class="font-bold text-slate-400">WIN RATE</p><p id="w" class="text-lg font-black text-green-500">0%</p></div>
        <div class="bg-white p-3 rounded-2xl shadow-sm"><p class="font-bold text-slate-400">P&L</p><p id="pnl" class="text-lg font-black text-blue-600">$0.00</p></div>
    </div>

    <div class="bg-white p-8 rounded-[2.5rem] shadow-sm mb-6 text-center border border-slate-100">
        <h2 id="pr" class="text-6xl font-black tracking-tighter text-slate-900 mb-2">$0.00</h2>
        <div class="flex justify-center gap-4 text-[10px] font-bold text-slate-400 uppercase">
            <span>RSI 1M: <b id="r1" class="text-slate-600">0</b></span>
            <span>RSI 3M: <b id="r3" class="text-slate-600">0</b></span>
        </div>
    </div>

    <div class="space-y-4 mb-6">
        <div class="bg-white p-5 rounded-2xl shadow-sm">
            <div class="flex justify-between items-center mb-3"><h3 class="font-black text-xs text-slate-700 uppercase">1m Analysis</h3><span id="s1" class="text-[9px] font-black px-3 py-1 rounded-full">WAIT</span></div>
            <div id="pats" class="flex flex-wrap gap-2"></div>
        </div>
    </div>

    <div class="rounded-2xl overflow-hidden h-60 shadow-lg mb-4">
        <iframe src="https://s.tradingview.com/widgetembed/?symbol=BITGET%3ASOLUSDT&interval=1&theme=light" width="100%" height="100%" frameborder="0"></iframe>
    </div>
    
    <p class="text-center text-[9px] font-bold text-slate-300 uppercase">Last Sync: <span id="ck">--:--:--</span></p>
</div>

<script>
    async function update() {
        try {
            const r = await fetch('/api/data'); const d = await r.json();
            if(d.price > 0) {
                document.getElementById('pr').innerText = '$' + d.price;
                document.getElementById('t').innerText = d.trades;
                document.getElementById('w').innerText = d.win;
                document.getElementById('pnl').innerText = d.pnl;
                document.getElementById('r1').innerText = d.r1;
                document.getElementById('r3').innerText = d.r3;
                document.getElementById('ck').innerText = d.upd;
                const s1 = document.getElementById('s1'); s1.innerText = d.s1;
                s1.className = d.s1 === 'BULLISH' ? 'text-[9px] font-black px-3 py-1 rounded-full bg-green-100 text-green-600' : 'text-[9px] font-black px-3 py-1 rounded-full bg-red-50 text-red-400';
                document.getElementById('pats').innerHTML = d.pats.map(p => `<span class="bg-emerald-50 text-green-700 px-2 py-0.5 rounded-lg text-[9px] font-bold border border-emerald-100">${p}</span>`).join('');
            }
        } catch(e) {}
    }
    setInterval(update, 4000); update();
</script>
</body></html>
"""

if __name__ == "__main__":
    app.run(host='0.0.0.0', port=int(os.environ.get('PORT', 10000)))
