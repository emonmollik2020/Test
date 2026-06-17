import ccxt
import pandas as pd
import ta
import time
import threading
import os
from flask import Flask, render_template_string, jsonify
from datetime import datetime, timezone

# ========== 1. কনফিগারেশন ==========
SYMBOL = "SOL/USDT"
INITIAL_FUND = 100.0
exchange = ccxt.bitget()

# ডাটা স্টোরেজ (গ্লোবাল ডিকশনারি)
bot_data = {
    "balance": INITIAL_FUND, "price": 0.0, "pnl": 0.0, "last_update": "Loading...",
    "in_position": False, "total_trades": 0, "win_rate": 0.0, "total_pnl": 0.0,
    "analysis_1m": {"rsi": 0.0, "ema20": 0.0, "signal": "WAIT", "patterns": []},
    "analysis_3m": {"rsi": 0.0, "macd": 0.0, "signal": "WAIT", "patterns": []},
    "wait_reason": "বিশ্লেষণ শুরু হচ্ছে...", "log": []
}

app = Flask(__name__)

# --- প্যাটার্ন ডিটেকশন লজিক ---
def get_patterns(df):
    pats = []
    if len(df) < 3: return pats
    o, h, l, c = df['o'].iloc[-1], df['h'].iloc[-1], df['l'].iloc[-1], df['c'].iloc[-1]
    po, pc = df['o'].iloc[-2], df['c'].iloc[-2]
    body = abs(c - o)
    if body > 0 and (min(c, o) - l) >= 1.8 * body: pats.append("Hammer")
    if pc < po and c > o and c >= po: pats.append("Bullish Engulfing")
    if abs(c - o) <= ((h - l) * 0.1): pats.append("Doji")
    return pats

# --- ট্রেডিং ইঞ্জিন (ব্যাকগ্রাউন্ড থ্রেড) ---
def bot_loop():
    global bot_data
    in_pos, holdings, entry_p = False, 0.0, 0.0
    wins, total, net_pnl = 0, 0, 0.0

    while True:
        try:
            # লাইভ ডাটা আনা
            bars1 = exchange.fetch_ohlcv(SYMBOL, '1m', limit=50)
            bars3 = exchange.fetch_ohlcv(SYMBOL, '3m', limit=50)
            df1 = pd.DataFrame(bars1, columns=['t','o','h','l','c','v'])
            df3 = pd.DataFrame(bars3, columns=['t','o','h','l','c','v'])
            price = df1['c'].iloc[-1]

            # ইন্ডিকেটর ক্যালকুলেশন
            rsi1 = ta.momentum.rsi(df1['c']).iloc[-1]
            ema20 = ta.trend.ema_indicator(df1['c'], window=20).iloc[-1]
            rsi3 = ta.momentum.rsi(df3['c']).iloc[-1]
            macd = ta.trend.macd(df3['c']).iloc[-1]
            macd_s = ta.trend.macd_signal(df3['c']).iloc[-1]

            # রিয়েল-টাইম ডাটা আপডেট
            bot_data.update({
                "price": round(price, 2),
                "last_update": datetime.now(timezone.utc).strftime("%H:%M:%S"),
                "analysis_1m": {"rsi": round(rsi1,1), "ema20": round(ema20,2), "signal": "BULLISH" if price > ema20 else "BEARISH", "patterns": get_patterns(df1)},
                "analysis_3m": {"rsi": round(rsi3,1), "macd": round(macd,3), "signal": "BULLISH" if macd > macd_s else "WAIT", "patterns": get_patterns(df3)},
                "in_position": in_pos
            })

            # বাই লজিক
            if not in_pos:
                if price > ema20 and rsi1 < 65 and macd > macd_s:
                    holdings = bot_data["balance"] / price
                    bot_data["balance"], entry_p, in_pos = 0.0, price, True
                    total += 1
                    bot_data["total_trades"] = total
                    bot_data["log"].insert(0, {"time": datetime.now().strftime("%H:%M"), "msg": f"BUY @ ${price:.2f}"})
                bot_data["wait_reason"] = "Trend is DOWN" if price <= ema20 else "Waiting for Confirmation"
            
            # সেল লজিক (TP 1.5%, SL 2.5%)
            elif in_pos:
                pnl_pct = (price / entry_p) - 1
                if pnl_pct >= 0.015 or pnl_pct <= -0.025:
                    bot_data["balance"] = holdings * price
                    in_pos = False
                    trade_pnl = bot_data["balance"] - INITIAL_FUND
                    if pnl_pct > 0: wins += 1
                    bot_data.update({"total_pnl": round(trade_pnl, 2), "win_rate": round((wins/total)*100, 1) if total > 0 else 0})
                    bot_data["log"].insert(0, {"time": datetime.now().strftime("%H:%M"), "msg": f"SELL @ ${price:.2f} ({pnl_pct*100:+.1f}%)"})

        except Exception as e:
            print(f"Error: {e}")
        time.sleep(10)

# --- ওয়েব রাউটস ---
@app.route('/api/data')
def get_api_data():
    return jsonify(bot_data)

@app.route('/')
def dashboard():
    return render_template_string(DASHBOARD_HTML)

# --- প্রফেশনাল ড্যাশবোর্ড ডিজাইন (HTML/JS) ---
DASHBOARD_HTML = """
<!DOCTYPE html>
<html lang="bn">
<head>
    <meta charset="UTF-8"><meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>SOL Pro Dashboard</title><script src="https://cdn.tailwindcss.com"></script>
    <style>
        .card { background: white; border-radius: 1.5rem; box-shadow: 0 10px 15px -3px rgba(0,0,0,0.05); border: 1px solid #f1f5f9; }
        .active-dot { height: 10px; width: 10px; background-color: #22c55e; border-radius: 50%; display: inline-block; animation: pulse 2s infinite; }
        @keyframes pulse { 0% { transform: scale(0.95); opacity: 0.7; } 70% { transform: scale(1.1); opacity: 1; } 100% { transform: scale(0.95); opacity: 0.7; } }
    </style>
</head>
<body class="bg-[#f8fafc] p-4 font-sans text-slate-800">
<div class="max-w-md mx-auto">
    <div class="text-center mb-6">
        <h1 class="text-2xl font-black text-slate-900 mb-1">SOL/USDT Trading Bot</h1>
        <div class="flex items-center justify-center gap-2">
            <span class="active-dot"></span><span class="text-xs font-bold text-green-600 uppercase tracking-widest">Bot is Live</span>
        </div>
    </div>

    <!-- Summary Section -->
    <div class="grid grid-cols-3 gap-3 mb-6 text-center">
        <div class="card p-3"><p class="text-[10px] font-bold text-slate-400 uppercase">Trades</p><p id="trades" class="text-xl font-black">0</p></div>
        <div class="card p-3"><p class="text-[10px] font-bold text-slate-400 uppercase">Win Rate</p><p id="rate" class="text-xl font-black text-green-500">0%</p></div>
        <div class="card p-3"><p class="text-[10px] font-bold text-slate-400 uppercase">Total P&L</p><p id="pnl" class="text-xl font-black text-blue-600">$0.00</p></div>
    </div>

    <!-- Price Card -->
    <div class="card p-6 mb-6">
        <div class="flex justify-between items-end mb-4">
            <h2 id="price" class="text-5xl font-black tracking-tighter text-slate-900">$0.00</h2>
            <div class="text-right text-xs font-bold text-slate-400">Balance: <span id="balance" class="text-slate-800">$100.00</span></div>
        </div>
        <div id="status_bar" class="w-full bg-orange-50 border border-orange-100 text-orange-600 py-2 rounded-2xl text-xs font-black text-center">
            ANALYZING MARKET...
        </div>
    </div>

    <!-- Analysis Boxes -->
    <div class="space-y-4 mb-6">
        <div class="card p-5">
            <div class="flex justify-between items-center mb-3"><h3 class="font-black text-sm text-slate-700 uppercase">1m Analysis</h3><span id="sig1" class="text-[10px] font-black px-3 py-1 rounded-full">WAIT</span></div>
            <div class="flex justify-between text-xs font-bold text-slate-500"><span>RSI: <b id="rsi1" class="text-slate-800">0</b></span><span>EMA20: <b id="ema20" class="text-slate-800">0</b></span></div>
            <div id="pats1" class="mt-3 flex flex-wrap gap-2"></div>
        </div>
        <div class="card p-5">
            <div class="flex justify-between items-center mb-3"><h3 class="font-black text-sm text-slate-700 uppercase">3m Analysis</h3><span id="sig3" class="text-[10px] font-black px-3 py-1 rounded-full">WAIT</span></div>
            <div class="flex justify-between text-xs font-bold text-slate-500"><span>RSI: <b id="rsi3" class="text-slate-800">0</b></span><span>MACD: <b id="macd3" class="text-slate-800">0</b></span></div>
            <div id="pats3" class="mt-3 flex flex-wrap gap-2"></div>
        </div>
    </div>

    <!-- Live Log -->
    <div class="card p-5 mb-6">
        <h3 class="font-black text-sm text-slate-700 uppercase mb-4">Live Logs</h3>
        <div id="logs" class="space-y-2"></div>
    </div>

    <!-- TradingView Chart -->
    <div class="card overflow-hidden h-64 shadow-inner">
        <iframe src="https://s.tradingview.com/widgetembed/?symbol=BITGET%3ASOLUSDT&interval=1&theme=light" width="100%" height="100%" frameborder="0"></iframe>
    </div>
    
    <p class="text-center text-[10px] font-bold text-slate-300 mt-6 uppercase tracking-widest">Last Server Update: <span id="clock">00:00:00</span></p>
</div>

<script>
    async function refresh() {
        try {
            const r = await fetch('/api/data'); const d = await r.json();
            document.getElementById('price').innerText = '$' + d.price;
            document.getElementById('balance').innerText = '$' + d.balance.toFixed(2);
            document.getElementById('trades').innerText = d.total_trades;
            document.getElementById('rate').innerText = d.win_rate + '%';
            document.getElementById('pnl').innerText = '$' + d.total_pnl.toFixed(2);
            document.getElementById('status_bar').innerText = d.wait_reason.toUpperCase();
            document.getElementById('clock').innerText = d.last_update;
            
            document.getElementById('rsi1').innerText = d.analysis_1m.rsi;
            document.getElementById('ema20').innerText = '$' + d.analysis_1m.ema20;
            const s1 = document.getElementById('sig1');
            s1.innerText = d.analysis_1m.signal;
            s1.className = d.analysis_1m.signal === 'BULLISH' ? 'text-[10px] font-black px-3 py-1 rounded-full bg-green-100 text-green-600' : 'text-[10px] font-black px-3 py-1 rounded-full bg-slate-100 text-slate-400';

            document.getElementById('rsi3').innerText = d.analysis_3m.rsi;
            document.getElementById('macd3').innerText = d.analysis_3m.macd;
            const s3 = document.getElementById('sig3');
            s3.innerText = d.analysis_3m.signal;
            s3.className = d.analysis_3m.signal === 'BULLISH' ? 'text-[10px] font-black px-3 py-1 rounded-full bg-blue-100 text-blue-600' : 'text-[10px] font-black px-3 py-1 rounded-full bg-slate-100 text-slate-400';

            document.getElementById('pats1').innerHTML = d.analysis_1m.patterns.map(p => `<span class="bg-emerald-50 text-green-700 px-2 py-0.5 rounded-lg text-[9px] font-bold border border-emerald-100">${p}</span>`).join('');
            document.getElementById('pats3').innerHTML = d.analysis_3m.patterns.map(p => `<span class="bg-emerald-50 text-green-700 px-2 py-0.5 rounded-lg text-[9px] font-bold border border-emerald-100">${p}</span>`).join('');
            document.getElementById('logs').innerHTML = d.log.slice(0,3).map(l => `<div class="flex justify-between text-[10px] font-bold"><span class="text-slate-400">${l.time}</span><span class="text-slate-600">${l.msg}</span></div>`).join('');
        } catch(e) {}
    }
    setInterval(refresh, 5000); refresh();
</script>
</body></html>
"""

if __name__ == "__main__":
    threading.Thread(target=bot_loop, daemon=True).start()
    app.run(host='0.0.0.0', port=int(os.environ.get('PORT', 10000)))
