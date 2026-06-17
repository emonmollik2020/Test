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
exchange = ccxt.bitget({'enableRateLimit': True})

# ড্যাশবোর্ড ডাটা (Global State)
bot_data = {
    "price": 0.0, "balance": 100.0, "pnl": 0.0, "last_update": "Syncing...",
    "status": "SEARCHING", "total_trades": 0, "win_rate": 0, "total_pnl": 0.0,
    "analysis_1m": {"rsi": 0, "ema": 0, "signal": "WAIT", "pats": []},
    "analysis_3m": {"rsi": 0, "macd": 0, "signal": "WAIT", "pats": []},
    "log": []
}

app = Flask(__name__)

def get_pats(df):
    p = []
    if len(df) < 3: return p
    o, h, l, c = df['o'].iloc[-1], df['h'].iloc[-1], df['l'].iloc[-1], df['c'].iloc[-1]
    po, pc = df['o'].iloc[-2], df['c'].iloc[-2]
    if abs(c-o) > 0 and (min(c,o)-l) >= 1.8*abs(c-o): p.append("Hammer")
    if pc < po and c > o and c >= po: p.append("Bullish Engulfing")
    return p

def bot_engine():
    global bot_data
    in_pos, holdings, entry_p = False, 0.0, 0.0
    wins, total, net_pnl = 0, 0, 0.0

    while True:
        try:
            # এক্সচেঞ্জ থেকে ডাটা আনা
            bars1 = exchange.fetch_ohlcv(SYMBOL, '1m', limit=50)
            bars3 = exchange.fetch_ohlcv(SYMBOL, '3m', limit=50)
            df1 = pd.DataFrame(bars1, columns=['t','o','h','l','c','v'])
            df3 = pd.DataFrame(bars3, columns=['t','o','h','l','c','v'])
            
            p = df1['c'].iloc[-1]
            r1 = ta.momentum.rsi(df1['c']).fillna(0).iloc[-1]
            e20 = ta.trend.ema_indicator(df1['c'], window=20).fillna(0).iloc[-1]
            r3 = ta.momentum.rsi(df3['c']).fillna(0).iloc[-1]
            m = ta.trend.macd(df3['c']).fillna(0).iloc[-1]
            ms = ta.trend.macd_signal(df3['c']).fillna(0).iloc[-1]

            # স্টেট আপডেট
            bot_data.update({
                "price": round(p, 2),
                "last_update": datetime.now(timezone.utc).strftime("%H:%M:%S"),
                "analysis_1m": {"rsi": round(r1,1), "ema": round(e20,2), "signal": "BULLISH" if p > e20 else "WAIT", "pats": get_pats(df1)},
                "analysis_3m": {"rsi": round(r3,1), "macd": round(m,3), "signal": "BULLISH" if m > ms else "WAIT", "pats": get_pats(df3)},
                "status": "TRADING" if in_pos else "SEARCHING"
            })

            # লজিক
            if not in_pos and p > e20 and r1 < 65 and m > ms:
                holdings = 100.0 / p
                entry_p, in_pos = p, True
                total += 1
                bot_data["total_trades"] = total
                bot_data["log"].insert(0, {"t": datetime.now().strftime("%H:%M"), "m": f"BUY @ ${p:.2f}"})
            
            elif in_pos:
                diff = (p / entry_p) - 1
                if diff >= 0.015 or diff <= -0.025:
                    in_pos = False
                    if diff > 0: wins += 1
                    net_pnl += (p - entry_p)
                    bot_data.update({"total_pnl": round(net_pnl, 2), "win_rate": round((wins/total)*100, 1)})
                    bot_data["log"].insert(0, {"t": datetime.now().strftime("%H:%M"), "m": f"SELL @ ${p:.2f}"})

        except: pass
        time.sleep(10)

# ইঞ্জিন চালু করা
threading.Thread(target=bot_engine, daemon=True).start()

@app.route('/api/data')
def api(): return jsonify(bot_data)

@app.route('/')
def index(): return render_template_string(UI)

UI = """
<!DOCTYPE html>
<html>
<head>
    <meta charset="UTF-8"><meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Live Bot</title><script src="https://cdn.tailwindcss.com"></script>
</head>
<body class="bg-[#f1f5f9] p-4 font-sans text-slate-700">
<div class="max-w-md mx-auto">
    <div class="text-center mb-6">
        <h1 class="text-2xl font-black text-slate-900 uppercase">SOL/USDT Trading</h1>
        <p class="text-green-600 text-[10px] font-bold tracking-widest mt-1">SERVER IS ACTIVE</p>
    </div>

    <div class="grid grid-cols-3 gap-3 mb-6 text-center">
        <div class="bg-white p-3 rounded-2xl shadow-sm"><p class="text-[9px] font-bold text-slate-400">TRADES</p><p id="t" class="text-lg font-black">0</p></div>
        <div class="bg-white p-3 rounded-2xl shadow-sm"><p class="text-[9px] font-bold text-slate-400">WIN RATE</p><p id="w" class="text-lg font-black text-green-500">0%</p></div>
        <div class="bg-white p-3 rounded-2xl shadow-sm"><p class="text-[9px] font-bold text-slate-400">P&L</p><p id="pnl" class="text-lg font-black text-blue-600">$0.00</p></div>
    </div>

    <div class="bg-white p-8 rounded-[2rem] shadow-sm mb-6 text-center border border-slate-100">
        <h2 id="pr" class="text-6xl font-black tracking-tighter text-slate-900 mb-2">$0.00</h2>
        <div class="flex justify-center gap-4 text-[10px] font-bold text-slate-400 uppercase">
            <span>RSI 1M: <b id="r1" class="text-slate-600">0</b></span>
            <span>RSI 3M: <b id="r3" class="text-slate-600">0</b></span>
        </div>
    </div>

    <div class="space-y-3 mb-6">
        <div class="bg-white p-4 rounded-2xl shadow-sm flex justify-between items-center">
            <span class="text-xs font-black text-slate-400 uppercase">1M Trend</span>
            <span id="s1" class="text-[10px] font-black px-3 py-1 rounded-full bg-slate-100">LOADING</span>
        </div>
        <div class="bg-white p-4 rounded-2xl shadow-sm flex justify-between items-center">
            <span class="text-xs font-black text-slate-400 uppercase">3M Signal</span>
            <span id="s3" class="text-[10px] font-black px-3 py-1 rounded-full bg-slate-100">LOADING</span>
        </div>
    </div>

    <div class="bg-white p-5 rounded-2xl shadow-sm mb-6">
        <h3 class="font-black text-[10px] text-slate-400 uppercase mb-3">Live Log</h3>
        <div id="lg" class="space-y-2"></div>
    </div>

    <div class="rounded-2xl overflow-hidden h-64 shadow-lg">
        <iframe src="https://s.tradingview.com/widgetembed/?symbol=BITGET%3ASOLUSDT&interval=1&theme=light" width="100%" height="100%" frameborder="0"></iframe>
    </div>
    <p class="text-center text-[9px] font-bold text-slate-300 mt-4 uppercase">Sync: <span id="ck">--:--:--</span></p>
</div>
<script>
    async function refresh() {
        try {
            const r = await fetch('/api/data'); const d = await r.json();
            if(d.price > 0) {
                document.getElementById('pr').innerText = '$' + d.price;
                document.getElementById('t').innerText = d.total_trades;
                document.getElementById('w').innerText = d.win_rate + '%';
                document.getElementById('pnl').innerText = '$' + d.total_pnl.toFixed(2);
                document.getElementById('r1').innerText = d.analysis_1m.rsi;
                document.getElementById('r3').innerText = d.analysis_3m.rsi;
                document.getElementById('ck').innerText = d.last_update;
                const s1 = document.getElementById('s1'); s1.innerText = d.analysis_1m.signal;
                s1.className = d.analysis_1m.signal === 'BULLISH' ? 'text-[10px] font-black px-3 py-1 rounded-full bg-green-100 text-green-600' : 'text-[10px] font-black px-3 py-1 rounded-full bg-red-50 text-red-400';
                const s3 = document.getElementById('s3'); s3.innerText = d.analysis_3m.signal;
                s3.className = d.analysis_3m.signal === 'BULLISH' ? 'text-[10px] font-black px-3 py-1 rounded-full bg-blue-100 text-blue-600' : 'text-[10px] font-black px-3 py-1 rounded-full bg-slate-50 text-slate-400';
                document.getElementById('lg').innerHTML = d.log.slice(0,3).map(l => `<div class="flex justify-between text-[10px] font-bold"><span class="text-slate-300">${l.t}</span><span class="text-slate-500">${l.m}</span></div>`).join('');
            }
        } catch(e) {}
    }
    setInterval(refresh, 3000); refresh();
</script>
</body></html>
"""

if __name__ == "__main__":
    app.run(host='0.0.0.0', port=int(os.environ.get('PORT', 10000)))
