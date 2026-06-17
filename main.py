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
            "analysis_1m": {"rsi": 0, "ema20": 0, "ema50": 0, "signal": "WAIT", "pats": []},
            "analysis_3m": {"rsi": 0, "macd": 0, "stoch": 0, "signal": "WAIT", "pats": []},
            "wait_reason": "বিশ্লেষণ চলছে...", "log": []
        }
    with open(STATE_FILE, "r") as f:
        return json.load(f)

app = Flask(__name__)

# --- প্যাটার্ন ডিটেকশন লজিক ---
def get_pats(df):
    p = []
    if len(df) < 3: return p
    o, h, l, c = df['o'].iloc[-1], df['h'].iloc[-1], df['l'].iloc[-1], df['c'].iloc[-1]
    po, pc = df['o'].iloc[-2], df['c'].iloc[-2]
    body = abs(c-o)
    if body > 0 and (min(c,o)-l) >= 1.8*body: p.append("হ্যামার &#128296;")
    if pc < po and c > o and c >= po: p.append("বুলিশ এনগালফিং &#128200;")
    if abs(c-o) <= ((h-l)*0.1): p.append("ডোজি &#9878;")
    return p

# --- ট্রেডিং ইঞ্জিন (ব্যাকগ্রাউন্ড) ---
def bot_engine():
    total, wins, net_pnl = 0, 0, 0.0
    in_pos, holdings, entry_p = False, 0.0, 0.0
    pnl_history = []

    while True:
        try:
            bars1 = exchange.fetch_ohlcv(SYMBOL, '1m', limit=50)
            bars3 = exchange.fetch_ohlcv(SYMBOL, '3m', limit=50)
            df1 = pd.DataFrame(bars1, columns=['t','o','h','l','c','v'])
            df3 = pd.DataFrame(bars3, columns=['t','o','h','l','c','v'])
            
            p = df1['c'].iloc[-1]
            r1 = ta.momentum.rsi(df1['c']).fillna(0).iloc[-1]
            e20 = ta.trend.ema_indicator(df1['c'], window=20).fillna(0).iloc[-1]
            e50 = ta.trend.ema_indicator(df1['c'], window=50).fillna(0).iloc[-1]
            r3 = ta.momentum.rsi(df3['c']).fillna(0).iloc[-1]
            macd = ta.trend.macd(df3['c']).fillna(0).iloc[-1]
            macd_s = ta.trend.macd_signal(df3['c']).fillna(0).iloc[-1]
            stoch = ta.momentum.stochrsi_k(df3['c']).fillna(0).iloc[-1] * 100

            # এন্ট্রি কন্ডিশন
            sig_1m = "বুলিশ" if p > e20 else "বেয়ারিশ"
            sig_3m = "বুলিশ" if macd > macd_s else "অপেক্ষা"
            
            current_state = load_state()
            current_state.update({
                "price": round(p, 2), "last_update": datetime.now(timezone.utc).strftime("%H:%M:%S"),
                "analysis_1m": {"rsi": round(r1,1), "ema20": round(e20,2), "ema50": round(e50,2), "signal": sig_1m, "pats": get_pats(df1)},
                "analysis_3m": {"rsi": round(r3,1), "macd": round(macd,3), "stoch": round(stoch,1), "signal": sig_3m, "pats": get_pats(df3)},
                "wait_reason": "এন্ট্রি খুঁজছে..." if not in_pos else "ট্রেডে আছে (IN POSITION)"
            })

            # বাই/সেল লজিক
            if not in_pos and p > e20 and r1 < 65 and macd > macd_s:
                holdings = current_state["balance"] / p
                current_state["balance"], entry_p, in_pos = 0.0, p, True
                total += 1
                current_state["total_trades"] = total
                current_state["log"].insert(0, {"t": datetime.now().strftime("%H:%M"), "m": f"BUY @ ${p:.2f}"})
            
            elif in_pos:
                diff = (p / entry_p) - 1
                if diff >= 0.015 or diff <= -0.025:
                    current_state["balance"] = holdings * p
                    in_pos = False
                    trade_pnl = current_state["balance"] - 100.0
                    pnl_history.append(trade_pnl)
                    if diff > 0: wins += 1
                    current_state.update({
                        "total_pnl": round(trade_pnl, 2),
                        "win_rate": round((wins/total)*100, 1),
                        "best": round(max(pnl_history), 2),
                        "worst": round(min(pnl_history), 2),
                        "last_action": "SELL"
                    })
                    current_state["log"].insert(0, {"t": datetime.now().strftime("%H:%M"), "m": f"SELL @ ${p:.2f}"})

            save_state(current_state)
        except: pass
        time.sleep(10)

threading.Thread(target=bot_engine, daemon=True).start()

@app.route('/api/data')
def api(): return jsonify(load_state())

@app.route('/')
def index(): return render_template_string(UI)

# --- আপনার পছন্দের প্রফেশনাল ডিজাইন ---
UI = """
<!DOCTYPE html>
<html>
<head>
    <meta charset="UTF-8"><meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Pro SOL Bot</title><script src="https://cdn.tailwindcss.com"></script>
    <style>
        .card { background: white; border-radius: 1.5rem; box-shadow: 0 4px 6px -1px rgba(0,0,0,0.05); border: 1px solid #f1f5f9; }
        .tag { background: #f0fdf4; color: #166534; padding: 2px 8px; border-radius: 20px; font-size: 9px; font-weight: 800; border: 1px solid #dcfce7; }
    </style>
</head>
<body class="bg-[#f8fafc] p-3 font-sans text-slate-800">
<div class="max-w-md mx-auto">
    <div class="text-center mb-6">
        <h1 class="text-xl font-black text-slate-700">&#129302; SOL/USDT ট্রেডিং বট</h1>
        <span class="bg-green-100 text-green-700 px-3 py-0.5 rounded-full text-[10px] font-bold mt-1 inline-block border border-green-200">&#9989; বট চলছে</span>
    </div>

    <!-- Summary Section -->
    <div class="grid grid-cols-3 gap-2 mb-3 text-center">
        <div class="card p-3"><p class="text-[9px] font-bold text-slate-400">মোট ট্রেড</p><p id="t" class="text-base font-black">0</p></div>
        <div class="card p-3"><p class="text-[9px] font-bold text-slate-400">জয়ের হার</p><p id="w" class="text-base font-black text-green-500">0%</p></div>
        <div class="card p-3"><p class="text-[9px] font-bold text-slate-400">মোট P&L</p><p id="pnl" class="text-base font-black text-blue-600">$0.00</p></div>
    </div>
    <div class="grid grid-cols-3 gap-2 mb-5 text-center">
        <div class="card p-2"><p class="text-[8px] font-bold text-slate-300 uppercase">সেরা</p><p id="bt" class="text-xs font-bold text-green-400">--</p></div>
        <div class="card p-2"><p class="text-[8px] font-bold text-slate-300 uppercase">খারাপ</p><p id="wt" class="text-xs font-bold text-red-400">--</p></div>
        <div class="card p-2"><p class="text-[8px] font-bold text-slate-300 uppercase">শেষ</p><p id="la" class="text-xs font-bold text-slate-500">---</p></div>
    </div>

    <!-- Price Section -->
    <div class="card p-6 mb-4 text-center">
        <div class="flex justify-between items-center mb-3">
            <span id="pr" class="text-4xl font-black text-slate-900">$0.00</span>
            <div class="text-right text-[10px] text-slate-400">ব্যালেন্স: <b id="bl" class="text-slate-700">$100.00</b></div>
        </div>
        <div id="st" class="w-full bg-orange-50 text-orange-600 py-2 rounded-xl text-[10px] font-black border border-orange-100 uppercase tracking-wider">
            ⌛ বিশ্লেষণ চলছে...
        </div>
    </div>

    <!-- Analysis Boxes -->
    <div class="card p-4 mb-3">
        <div class="flex justify-between items-center mb-2"><h3 class="font-bold text-slate-700 text-xs">&#128202; 1 মিনিট বিশ্লেষণ</h3><span id="s1" class="text-[9px] font-bold px-2 py-0.5 rounded bg-slate-100">WAIT</span></div>
        <div class="grid grid-cols-3 text-[10px] text-slate-500">
            <p>RSI: <b id="r1" class="text-slate-800">0</b></p>
            <p>E20: <b id="e20" class="text-slate-800">0</b></p>
            <p>E50: <b id="e50" class="text-slate-800">0</b></p>
        </div>
        <div id="p1" class="mt-2 flex flex-wrap gap-1"></div>
    </div>

    <div class="card p-4 mb-4">
        <div class="flex justify-between items-center mb-2"><h3 class="font-bold text-slate-700 text-xs">&#128202; 3 মিনিট বিশ্লেষণ</h3><span id="s3" class="text-[9px] font-bold px-2 py-0.5 rounded bg-slate-100">WAIT</span></div>
        <div class="grid grid-cols-3 text-[10px] text-slate-500">
            <p>RSI: <b id="r3" class="text-slate-800">0</b></p>
            <p>MACD: <b id="m3" class="text-slate-800">0</b></p>
            <p>STOCH: <b id="sk3" class="text-slate-800">0</b></p>
        </div>
        <div id="p3" class="mt-2 flex flex-wrap gap-1"></div>
    </div>

    <div class="rounded-2xl overflow-hidden h-56 shadow-lg mb-4 border border-slate-100">
        <iframe src="https://s.tradingview.com/widgetembed/?symbol=BITGET%3ASOLUSDT&interval=1&theme=light" width="100%" height="100%" frameborder="0"></iframe>
    </div>

    <div class="card p-4 mb-4">
        <h3 class="font-bold text-slate-700 text-[10px] uppercase mb-2">&#128203; লাইভ লগ</h3>
        <div id="lg" class="space-y-1.5"></div>
    </div>
    
    <p class="text-center text-[9px] font-bold text-slate-300 uppercase tracking-tighter">Sync: <span id="ck">--:--:--</span></p>
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
                document.getElementById('pnl').innerText = d.pnl;
                document.getElementById('bt').innerText = '$' + d.best;
                document.getElementById('wt').innerText = '$' + d.worst;
                document.getElementById('la').innerText = d.last_action;
                document.getElementById('st').innerText = '⌛ ' + d.wait_reason;
                document.getElementById('ck').innerText = d.last_update;
                
                document.getElementById('r1').innerText = d.analysis_1m.rsi;
                document.getElementById('e20').innerText = d.analysis_1m.ema20;
                document.getElementById('e50').innerText = d.analysis_1m.ema50;
                const s1 = document.getElementById('s1'); s1.innerText = d.analysis_1m.signal;
                s1.className = d.analysis_1m.signal === 'বুলিশ' ? 'text-[9px] font-bold px-2 py-0.5 rounded bg-green-100 text-green-600' : 'text-[9px] font-bold px-2 py-0.5 rounded bg-red-50 text-red-400';

                document.getElementById('r3').innerText = d.analysis_3m.rsi;
                document.getElementById('m3').innerText = d.analysis_3m.macd;
                document.getElementById('sk3').innerText = d.analysis_3m.stoch;
                const s3 = document.getElementById('s3'); s3.innerText = d.analysis_3m.signal;
                s3.className = d.analysis_3m.signal === 'বুলিশ' ? 'text-[9px] font-bold px-2 py-0.5 rounded bg-blue-100 text-blue-600' : 'text-[9px] font-bold px-2 py-0.5 rounded bg-slate-50 text-slate-400';

                document.getElementById('p1').innerHTML = d.analysis_1m.pats.map(p => `<span class="tag">${p}</span>`).join('');
                document.getElementById('p3').innerHTML = d.analysis_3m.pats.map(p => `<span class="tag">${p}</span>`).join('');
                document.getElementById('lg').innerHTML = d.log.slice(0,3).map(l => `<div class="flex justify-between text-[10px] font-bold border-b border-slate-50 pb-1"><span class="text-slate-300">${l.t}</span><span class="text-slate-500">${l.m}</span></div>`).join('');
            }
        } catch(e) {}
    }
    setInterval(update, 4000); update();
</script>
</body></html>
"""

if __name__ == "__main__":
    app.run(host='0.0.0.0', port=int(os.environ.get('PORT', 10000)))
