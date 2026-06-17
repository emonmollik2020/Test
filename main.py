import ccxt
import pandas as pd
import ta
import time
import threading
import os
from flask import Flask, render_template_string, jsonify
from datetime import datetime, timezone

# ========== ১. কনফিগারেশন ==========
SYMBOL = "SOL/USDT"
INITIAL_FUND = 100.0
TP_PCT = 0.015  # ১.৫% লাভ
SL_PCT = 0.025  # ২.৫% লস
CHECK_INTERVAL = 10 

exchange = ccxt.bitget({'enableRateLimit': True})

# ড্যাশবোর্ড ডাটা স্টোরেজ
bot_data = {
    "balance": INITIAL_FUND, "price": 0.0, "pnl": 0.0, "last_update": "Loading...",
    "in_position": False, "total_trades": 0, "win_rate": 0, "total_pnl": 0.0,
    "analysis_1m": {"rsi": 0.0, "ema20": 0.0, "signal": "WAIT", "patterns": []},
    "analysis_3m": {"rsi": 0.0, "macd": 0.0, "signal": "WAIT", "patterns": []},
    "wait_reason": "বিশ্লেষণ শুরু হচ্ছে...", "log": []
}

app = Flask(__name__)

# --- প্যাটার্ন ডিটেকশন ---
def get_patterns(df):
    pats = []
    try:
        if len(df) < 3: return pats
        o, h, l, c = df['o'].iloc[-1], df['h'].iloc[-1], df['l'].iloc[-1], df['c'].iloc[-1]
        po, pc = df['o'].iloc[-2], df['c'].iloc[-2]
        body = abs(c - o)
        if body > 0 and (min(c, o) - l) >= 1.8 * body: pats.append("Hammer")
        if pc < po and c > o and c >= po: pats.append("Bullish Engulfing")
        if abs(c - o) <= ((h - l) * 0.1): pats.append("Doji")
    except: pass
    return pats

# --- ট্রেডিং লজিক ইঞ্জিন ---
def bot_loop():
    global bot_data
    in_pos, holdings, entry_p = False, 0.0, 0.0
    wins, total, net_pnl = 0, 0, 0.0

    while True:
        try:
            # ডাটা সংগ্রহ
            bars1 = exchange.fetch_ohlcv(SYMBOL, '1m', limit=100)
            bars3 = exchange.fetch_ohlcv(SYMBOL, '3m', limit=100)
            df1 = pd.DataFrame(bars1, columns=['t','o','h','l','c','v'])
            df3 = pd.DataFrame(bars3, columns=['t','o','h','l','c','v'])
            
            if df1.empty: continue
            price = df1['c'].iloc[-1]

            # ইন্ডিকেটর ক্যালকুলেশন
            rsi1 = ta.momentum.rsi(df1['c']).fillna(0).iloc[-1]
            ema20 = ta.trend.ema_indicator(df1['c'], window=20).fillna(0).iloc[-1]
            rsi3 = ta.momentum.rsi(df3['c']).fillna(0).iloc[-1]
            macd = ta.trend.macd(df3['c']).fillna(0).iloc[-1]
            macd_s = ta.trend.macd_signal(df3['c']).fillna(0).iloc[-1]

            # ডাটা আপডেট
            bot_data.update({
                "price": round(price, 2),
                "last_update": datetime.now(timezone.utc).strftime("%H:%M:%S"),
                "analysis_1m": {"rsi": round(rsi1,1), "ema20": round(ema20,2), "signal": "BULLISH" if price > ema20 else "BEARISH", "patterns": get_patterns(df1)},
                "analysis_3m": {"rsi": round(rsi3,1), "macd": round(macd,3), "signal": "BULLISH" if macd > macd_s else "WAIT", "patterns": get_patterns(df3)},
                "in_position": in_pos
            })

            # লজিক
            if not in_pos:
                if price > ema20 and rsi1 < 65 and macd > macd_s:
                    holdings = bot_data["balance"] / price
                    bot_data["balance"], entry_p, in_pos = 0.0, price, True
                    total += 1
                    bot_data["total_trades"] = total
                    bot_data["log"].insert(0, {"time": datetime.now().strftime("%H:%M"), "msg": f"BUY @ ${price:.2f}"})
                bot_data["wait_reason"] = "Trend is DOWN" if price <= ema20 else "Wait for MACD Cross"
            elif in_pos:
                pnl_pct = (price / entry_p) - 1
                if pnl_pct >= TP_PCT or pnl_pct <= -SL_PCT:
                    bot_data["balance"] = holdings * price
                    in_pos = False
                    trade_pnl = bot_data["balance"] - INITIAL_FUND
                    if pnl_pct > 0: wins += 1
                    bot_data.update({"total_pnl": round(trade_pnl, 2), "win_rate": round((wins/total)*100, 1) if total > 0 else 0})
                    bot_data["log"].insert(0, {"time": datetime.now().strftime("%H:%M"), "msg": f"SELL @ ${price:.2f} ({pnl_pct*100:+.1f}%)"})

        except: pass
        time.sleep(CHECK_INTERVAL)

@app.route('/api/data')
def api(): return jsonify(bot_data)

@app.route('/')
def home(): return render_template_string(DASHBOARD_HTML)

# --- ড্যাশবোর্ড ডিজাইন (HTML Entities সহ) ---
DASHBOARD_HTML = """
<!DOCTYPE html>
<html>
<head>
    <meta charset="UTF-8"><meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>SOL/USDT Trading Bot</title><script src="https://cdn.tailwindcss.com"></script>
    <style>
        .card { background: white; border-radius: 1.5rem; box-shadow: 0 10px 15px -3px rgba(0,0,0,0.05); border: 1px solid #f1f5f9; }
        .tag { background: #f0fdf4; color: #166534; padding: 2px 8px; border-radius: 20px; font-size: 10px; font-weight: 800; border: 1px solid #dcfce7; }
    </style>
</head>
<body class="bg-[#f8fafc] p-4 font-sans text-slate-800">
<div class="max-w-md mx-auto">
    <div class="text-center mb-6">
        <h1 class="text-2xl font-black text-slate-900 mb-1">&#129302; SOL/USDT Trading</h1>
        <p class="text-green-600 text-[10px] font-bold uppercase tracking-widest">&#9989; BOT IS LIVE</p>
    </div>

    <div class="grid grid-cols-3 gap-3 mb-6 text-center">
        <div class="card p-3"><p class="text-[9px] font-bold text-slate-400 uppercase">Trades</p><p id="t" class="text-lg font-black">0</p></div>
        <div class="card p-3"><p class="text-[9px] font-bold text-slate-400 uppercase">Win Rate</p><p id="w" class="text-lg font-black text-green-500">0%</p></div>
        <div class="card p-3"><p class="text-[9px] font-bold text-slate-400 uppercase">P&L</p><p id="pnl" class="text-lg font-black text-blue-600">$0.00</p></div>
    </div>

    <div class="card p-6 mb-6 text-center">
        <div class="flex justify-between items-end mb-4">
            <h2 id="pr" class="text-5xl font-black tracking-tighter text-slate-900">$0.00</h2>
            <div class="text-right text-[10px] font-bold text-slate-400">Balance: <span id="bl" class="text-slate-800">$100.00</span></div>
        </div>
        <div id="st" class="w-full bg-orange-50 border border-orange-100 text-orange-600 py-2 rounded-2xl text-[11px] font-black uppercase italic">
            Connecting to Bitget...
        </div>
    </div>

    <div class="space-y-4 mb-6">
        <div class="card p-5">
            <div class="flex justify-between items-center mb-3"><h3 class="font-black text-xs text-slate-700 uppercase">&#128202; 1m Analysis</h3><span id="s1" class="text-[9px] font-black px-3 py-1 rounded-full">WAIT</span></div>
            <div class="flex justify-around text-xs font-bold text-slate-500"><span>RSI: <b id="r1" class="text-slate-800">0</b></span><span>EMA20: <b id="e1" class="text-slate-800">0</b></span></div>
            <div id="p1" class="mt-3 flex justify-center flex-wrap gap-2"></div>
        </div>
        <div class="card p-5">
            <div class="flex justify-between items-center mb-3"><h3 class="font-black text-xs text-slate-700 uppercase">&#128202; 3m Analysis</h3><span id="s3" class="text-[9px] font-black px-3 py-1 rounded-full">WAIT</span></div>
            <div class="flex justify-around text-xs font-bold text-slate-500"><span>RSI: <b id="r3" class="text-slate-800">0</b></span><span>MACD: <b id="m3" class="text-slate-800">0</b></span></div>
            <div id="p3" class="mt-3 flex justify-center flex-wrap gap-2"></div>
        </div>
    </div>

    <div class="card p-5 mb-6"><h3 class="font-black text-xs text-slate-700 uppercase mb-4">&#128203; Live Logs</h3><div id="lg" class="space-y-2"></div></div>
    <div class="card overflow-hidden h-64"><iframe src="https://s.tradingview.com/widgetembed/?symbol=BITGET%3ASOLUSDT&interval=1&theme=light" width="100%" height="100%" frameborder="0"></iframe></div>
    <p class="text-center text-[9px] font-bold text-slate-300 mt-6 uppercase tracking-widest">Update: <span id="ck">--:--:--</span></p>
</div>

<script>
    async function up() {
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
                document.getElementById('r1').innerText = d.analysis_1m.rsi;
                document.getElementById('e1').innerText = '$' + d.analysis_1m.ema20;
                const s1 = document.getElementById('s1'); s1.innerText = d.analysis_1m.signal;
                s1.className = d.analysis_1m.signal === 'BULLISH' ? 'text-[9px] font-black px-3 py-1 rounded-full bg-green-100 text-green-600' : 'text-[9px] font-black px-3 py-1 rounded-full bg-red-100 text-red-500';
                document.getElementById('r3').innerText = d.analysis_3m.rsi;
                document.getElementById('m3').innerText = d.analysis_3m.macd;
                const s3 = document.getElementById('s3'); s3.innerText = d.analysis_3m.signal;
                s3.className = d.analysis_3m.signal === 'BULLISH' ? 'text-[9px] font-black px-3 py-1 rounded-full bg-blue-100 text-blue-600' : 'text-[9px] font-black px-3 py-1 rounded-full bg-slate-100 text-slate-400';
                document.getElementById('p1').innerHTML = d.analysis_1m.patterns.map(p => `<span class="tag">${p}</span>`).join('');
                document.getElementById('p3').innerHTML = d.analysis_3m.patterns.map(p => `<span class="tag">${p}</span>`).join('');
                document.getElementById('lg').innerHTML = d.log.slice(0,3).map(l => `<div class="flex justify-between text-[10px] font-bold"><span class="text-slate-400">${l.time}</span><span class="text-slate-600">${l.msg}</span></div>`).join('');
            }
        } catch(e) {}
    }
    setInterval(up, 4000); up();
</script>
</body></html>
"""

if __name__ == "__main__":
    threading.Thread(target=bot_loop, daemon=True).start()
    app.run(host='0.0.0.0', port=int(os.environ.get('PORT', 8080)))
