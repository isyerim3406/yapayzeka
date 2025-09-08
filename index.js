// index.js (güncellenmiş)
// Gerekli bağımlılıkları içe aktarın
import WebSocket from 'ws';
import dotenv from 'dotenv';
import express from 'express';
import fetch from 'node-fetch';
import fs from 'fs';
import path from 'path';
import pkg from 'binance-api-node';
const Binance = pkg.default || pkg;

dotenv.config();

const app = express();
const PORT = process.env.PORT || 3000;

// =========================================================================================
// MACD BOT STRATEJİ SINIFI (düzeltilmiş EMA/MACD hizalama ile)
// =========================================================================================
class MACDBotStrategy {
    constructor(options = {}) {
        this.shortPeriod = options.shortPeriod || 12;
        this.longPeriod = options.longPeriod || 26;
        this.signalPeriod = options.signalPeriod || 9;
        this.trendAnalysisBars = options.trendAnalysisBars || 50;

        // Internal state
        this.klines = [];
        this.macdLine = [];
        this.signalLine = [];
    }

    // Üstel Hareketli Ortalama (EMA) hesaplama
    // returns array same length as prices, with leading nulls where EMA not defined
    calculateEMA(prices, period) {
        const k = 2 / (period + 1);
        const ema = new Array(prices.length).fill(null);
        let sum = 0;

        for (let i = 0; i < prices.length; i++) {
            const p = Number(prices[i]);
            if (i < period - 1) {
                sum += p;
                ema[i] = null;
            } else if (i === period - 1) {
                sum += p;
                const first = sum / period;
                ema[i] = first;
            } else {
                // use previous EMA (which must exist at i-1)
                const prev = ema[i - 1];
                ema[i] = p * k + prev * (1 - k);
            }
        }
        return ema;
    }

    // MACD ve Sinyal Hattı hesaplama (aligned)
    calculateMACD(data) {
        const len = data.length;
        if (len === 0) {
            this.macdLine = [];
            this.signalLine = [];
            return;
        }

        const prices = data.map(d => Number(d.close));
        // produce EMA arrays aligned with prices
        const shortEMA = this.calculateEMA(prices, this.shortPeriod); // same length
        const longEMA = this.calculateEMA(prices, this.longPeriod);   // same length

        const macdLine = new Array(len).fill(null);
        for (let i = 0; i < len; i++) {
            if (shortEMA[i] !== null && longEMA[i] !== null) {
                macdLine[i] = shortEMA[i] - longEMA[i];
            } else {
                macdLine[i] = null;
            }
        }

        // calculate signal line (EMA on macd values) aligned with macdLine
        // create compact array of macd values (no nulls), calc EMA, then map back
        const compact = [];
        const compactIdx = [];
        for (let i = 0; i < macdLine.length; i++) {
            if (macdLine[i] !== null) {
                compactIdx.push(i);
                compact.push(macdLine[i]);
            }
        }

        let compactSignal = [];
        if (compact.length > 0) {
            compactSignal = this.calculateEMA(compact, this.signalPeriod);
        }

        const signalLine = new Array(len).fill(null);
        for (let j = 0; j < compactIdx.length; j++) {
            const idx = compactIdx[j];
            signalLine[idx] = compactSignal[j] !== undefined ? compactSignal[j] : null;
        }

        this.macdLine = macdLine;
        this.signalLine = signalLine;
    }

    async getTrendAnalysis() {
        if (this.klines.length < this.trendAnalysisBars) return "Unknown";
        const trendData = this.klines.slice(-this.trendAnalysisBars).map(k => k.close);
        const apiKey = process.env.GEMINI_API_KEY;
        const apiUrl = `https://generativelanguage.googleapis.com/v1beta/models/gemini-2.5-flash-preview-05-20:generateContent?key=${apiKey}`;
        const prompt = `Analiz the following financial data points and determine if the general trend is 'Bullish', 'Bearish', or 'Sideways'. Only respond with a single word: Bullish, Bearish, or Sideways. Data points: ${trendData.join(', ')}`;
        
        const payload = {
            contents: [{ parts: [{ text: prompt }] }],
            tools: [{ "google_search": {} }],
        };

        try {
            const response = await fetch(apiUrl, {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify(payload)
            });

            if (!response.ok) {
                const errorText = await response.text();
                console.error("API request failed. Status Code:", response.status, "Error Text:", errorText);
                return "Unknown";
            }
            
            const result = await response.json();
            const trend = result.candidates?.[0]?.content?.parts?.[0]?.text?.trim() || 'Unknown';
            return trend;
        } catch (error) {
            console.error("Error during Gemini API call:", error);
            return "Unknown";
        }
    }

    // processCandle now updates existing bar if timestamp matches (prevents duplicates)
    async processCandle(timestamp, open, high, low, close) {
        // timestamp expected in ms (integer)
        const existingBarIndex = this.klines.findIndex(k => k.timestamp === timestamp);
        if (existingBarIndex !== -1) {
            // update existing
            this.klines[existingBarIndex] = { timestamp, open, high, low, close };
        } else {
            // append new
            this.klines.push({ timestamp, open, high, low, close });
            if (this.klines.length > 2000) this.klines.shift(); // keep reasonable size
        }

        this.calculateMACD(this.klines);

        // find last two indices where macd and signal are both non-null
        const idxs = [];
        for (let i = this.macdLine.length - 1; i >= 0 && idxs.length < 2; i--) {
            if (this.macdLine[i] !== null && this.signalLine[i] !== null) idxs.push(i);
        }
        if (idxs.length < 2) return { signal: null };

        const lastIdx = idxs[0];
        const prevIdx = idxs[1];

        const lastMACD = this.macdLine[lastIdx];
        const prevMACD = this.macdLine[prevIdx];
        const lastSignal = this.signalLine[lastIdx];
        const prevSignal = this.signalLine[prevIdx];

        let signalType = null;
        let trend = null;
        let message = null;

        if (prevMACD < prevSignal && lastMACD > lastSignal) {
            signalType = 'BUY';
            trend = await this.getTrendAnalysis();
            message = `MACD Sinyali: AL - TrendAI tarafından onaylandı: ${trend}`;
        } else if (prevMACD > prevSignal && lastMACD < lastSignal) {
            signalType = 'SELL';
            trend = await this.getTrendAnalysis();
            message = `MACD Sinyali: SAT - TrendAI tarafından onaylandı: ${trend}`;
        }

        if (signalType) {
            if (trend && trend.toLowerCase && trend.toLowerCase() === 'sideways') {
                return {
                    signal: {
                        type: 'REJECTED',
                        signalType,
                        message: `Sinyal Reddedildi: Piyasa yatay olduğu için yeni pozisyon açılmadı. TrendAI trendi '${trend}' olarak belirledi.`
                    }
                };
            } else {
                return {
                    signal: {
                        type: 'CONFIRMED',
                        signalType,
                        message
                    }
                };
            }
        }

        return { signal: null };
    }
}

// =========================================================================================
// STRATEGY CONFIGURATION
// =========================================================================================
const CFG = {
    SYMBOL: process.env.SYMBOL || 'ETHUSDT', // accept 'BINANCE:ETHUSDT' or 'ETHUSDT'
    INTERVAL: process.env.INTERVAL || '1m',  // use Binance-style interval (1m, 5m, 1h, 1d)
    TG_TOKEN: process.env.TG_TOKEN,
    TG_CHAT_ID: process.env.TG_CHAT_ID,
    IS_TESTNET: process.env.IS_TESTNET === 'true',
};

// normalize symbol
function normalizeSymbol(s) {
    if (!s) return 'ETHUSDT';
    if (s.includes(':')) return s.split(':')[1];
    return s;
}
const SYMBOL_BINANCE = normalizeSymbol(CFG.SYMBOL);

// =========================================================================================
// GLOBAL STATE
// =========================================================================================
let botCurrentPosition = 'none';
let botEntryPrice = 0;
let totalNetProfit = 0;
let isBotInitialized = false;
let lastKnownCloseTime = 0; // ms epoch of last processed closed bar

const isSimulationMode = !process.env.BINANCE_API_KEY || !process.env.BINANCE_SECRET_KEY;

// Mock Binance Client for simulation mode
const mockBinanceClient = {
    futuresAccountBalance: async () => [{ asset: 'USDT', availableBalance: '1000' }],
    futuresMarketOrder: async ({ side, quantity }) => {
        console.log(`[SİMÜLASYON] ${side} emri başarıyla oluşturuldu: ${quantity}`);
        return { status: 'FILLED' };
    },
    candles: async ({ symbol, interval, limit }) => {
        const mockCandles = [];
        let price = 4300;
        let now = Date.now();
        for (let i = 0; i < limit; i++) {
            const open = price;
            const close = open + (Math.random() - 0.5) * 10;
            mockCandles.push({
                open: open.toFixed(2),
                high: Math.max(open, close).toFixed(2),
                low: Math.min(open, close).toFixed(2),
                close: close.toFixed(2),
                closeTime: now - (limit - i) * 60 * 1000,
                volume: (1000 + Math.random() * 500).toFixed(2),
            });
            price = close;
        }
        return mockCandles;
    },
    prices: async ({ symbol }) => {
        const lastKline = macdBotStrategy.klines[macdBotStrategy.klines.length - 1];
        const lastPrice = lastKline ? lastKline.close : 4300;
        return { [symbol]: lastPrice.toString() };
    }
};

const binanceClient = isSimulationMode ? mockBinanceClient : Binance({
    apiKey: process.env.BINANCE_API_KEY,
    apiSecret: process.env.BINANCE_SECRET_KEY,
    test: CFG.IS_TESTNET,
});

// MACD parametrelerini ENV'den alacak şekilde güncellendi
const macdBotStrategy = new MACDBotStrategy({
    shortPeriod: parseInt(process.env.MACD_SHORT_PERIOD, 10) || 12,
    longPeriod: parseInt(process.env.MACD_LONG_PERIOD, 10) || 26,
    signalPeriod: parseInt(process.env.MACD_SIGNAL_PERIOD, 10) || 9,
    trendAnalysisBars: parseInt(process.env.TREND_ANALYSIS_BARS, 10) || 50
});

// =========================================================================================
// TELEGRAM (değişmedi)
// =========================================================================================
async function sendTelegramMessage(text) {
    if (!CFG.TG_TOKEN || !CFG.TG_CHAT_ID) {
        console.warn('Telegram API token or chat ID not set. Skipping message.');
        return;
    }
    const telegramApiUrl = `https://api.telegram.org/bot${CFG.TG_TOKEN}/sendMessage`;
    const payload = {
        chat_id: CFG.TG_CHAT_ID,
        text: text,
        parse_mode: 'Markdown'
    };
    try {
        const response = await fetch(telegramApiUrl, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify(payload)
        });
        if (!response.ok) {
            console.error(`Telegram API error: ${response.status} ${response.statusText}`);
        }
    } catch (error) {
        console.error('Failed to send Telegram message:', error);
    }
}

// =========================================================================================
// ORDER PLACEMENT & TRADING LOGIC (mantık aynı; küçük koruma eklendi)
// =========================================================================================
async function placeOrder(side, signalMessage) {
    const lastClosePrice = macdBotStrategy.klines[macdBotStrategy.klines.length - 1]?.close || 0;

    // Önceki pozisyonu kapat (varsa farklı yöndeyse)
    if (botCurrentPosition !== 'none' && botCurrentPosition !== side.toLowerCase()) {
        try {
            const pnl = botCurrentPosition === 'long' ? (lastClosePrice - botEntryPrice) : (botEntryPrice - lastClosePrice);
            totalNetProfit += pnl;

            const profitMessage = pnl >= 0 ? `+${pnl.toFixed(2)} USDT` : `${pnl.toFixed(2)} USDT`;
            const positionCloseMessage = `📉 Pozisyon kapatıldı! ${botCurrentPosition.toUpperCase()}\n\nSon Kapanış Fiyatı: ${lastClosePrice}\nBu İşlemden Kâr/Zarar: ${profitMessage}\n**Toplam Net Kâr: ${totalNetProfit.toFixed(2)} USDT**`;
            sendTelegramMessage(positionCloseMessage);

            console.log(`[SİMÜLASYON] Mevcut pozisyon (${botCurrentPosition}) kapatıldı.`);
            botCurrentPosition = 'none'; // Pozisyonu sıfırla
        } catch (error) {
            console.error('Mevcut pozisyonu kapatırken hata oluştu:', error);
            return;
        }
    }

    // Yeni pozisyonu aç
    if (botCurrentPosition === 'none' || botCurrentPosition !== side.toLowerCase()) {
        try {
            const currentPrice = lastClosePrice;
            let quantity = (100 * (100 / 100)) / (currentPrice || 1); // Sabit bir sermaye kullanarak miktar hesapla
            
            console.log(`[SİMÜLASYON] ${side} emri verildi. Fiyat: ${currentPrice}`);
            
            botCurrentPosition = side.toLowerCase();
            botEntryPrice = currentPrice;
            const positionMessage = `🚀 **${side} Emri Gerçekleşti!**\n\n**Sinyal:** ${signalMessage}\n**Fiyat:** ${currentPrice}\n**Miktar:** ${quantity.toFixed(4)}\n**Toplam Net Kâr: ${totalNetProfit.toFixed(2)} USDT**`;
            sendTelegramMessage(positionMessage);
        } catch (error) {
            console.error('Emir verirken hata oluştu:', error);
        }
    }
}

// =========================================================================================
// DATA HANDLING (backfill + WS handling)
// =========================================================================================

// Try to load tv_bars.json (export from TradingView) for perfect backfill alignment
async function loadTvBackfillIfExists() {
    const tvPath = path.join(process.cwd(), 'tv_bars.json');
    if (fs.existsSync(tvPath)) {
        try {
            const raw = fs.readFileSync(tvPath, 'utf8');
            const bars = JSON.parse(raw);
            // bars expected to have timestamp (ms) or datetime string
            bars.forEach(b => {
                let ts = b.timestamp ?? b.time ?? b.closeTime ?? b.datetime ?? b.date;
                if (typeof ts === 'string') ts = new Date(ts).getTime();
                macdBotStrategy.klines.push({
                    timestamp: Number(ts),
                    open: Number(b.open),
                    high: Number(b.high),
                    low: Number(b.low),
                    close: Number(b.close)
                });
            });
            console.log(`✅ tv_bars.json bulundu ve ${macdBotStrategy.klines.length} bar yüklendi.`);
            return true;
        } catch (e) {
            console.error('tv_bars.json okunurken hata:', e);
            return false;
        }
    }
    return false;
}

async function fetchInitialData() {
    try {
        const usedTv = await loadTvBackfillIfExists();
        if (!usedTv) {
            // Fallback: Binance REST candles
            console.log('tv_bars.json yok. Binance REST ile backfill alınıyor...');
            const limit = 500;
            const klines = await binanceClient.candles({
                symbol: SYMBOL_BINANCE,
                interval: CFG.INTERVAL,
                limit
            });
            // klines: objects with closeTime field (mock or binance-api-node format)
            macdBotStrategy.klines = klines.map(k => ({
                timestamp: Number(k.closeTime ?? k.close_time ?? k[6] ?? Date.now()),
                open: Number(k.open ?? k[1]),
                high: Number(k.high ?? k[2]),
                low: Number(k.low ?? k[3]),
                close: Number(k.close ?? k[4])
            }));
            console.log(`✅ Binance REST backfill: ${macdBotStrategy.klines.length} bar yüklendi.`);
        }

        // set lastKnownCloseTime to last bar's timestamp to avoid re-processing duplicates
        lastKnownCloseTime = macdBotStrategy.klines[macdBotStrategy.klines.length - 1]?.timestamp || 0;

        if (!isBotInitialized) {
            macdBotStrategy.calculateMACD(macdBotStrategy.klines);
            const macdStatus = getMACDStatus(macdBotStrategy.macdLine, macdBotStrategy.signalLine);
            const initialMessage = `✅ Bot başlatıldı!\n\n**Mod:** ${isSimulationMode ? 'Simülasyon' : 'Canlı İşlem'}\n**Sembol:** ${SYMBOL_BINANCE}\n**Zaman Aralığı:** ${CFG.INTERVAL}\n\n**MACD'nin Şu Anki Durumu:** ${macdStatus}`;
            sendTelegramMessage(initialMessage);
            isBotInitialized = true;
        }

    } catch (error) {
        console.error('İlk verileri çekerken hata:', error);
    }
}

// Helper function to get MACD status
function getMACDStatus(macdLine, signalLine) {
    if (!macdLine || !signalLine || macdLine.length < 1 || signalLine.length < 1) {
        return "Durum Belirlenemedi";
    }
    // find last index where both exist
    for (let i = macdLine.length - 1; i >= 0; i--) {
        if (macdLine[i] !== null && signalLine[i] !== null) {
            const lastMACD = macdLine[i];
            const lastSignal = signalLine[i];
            if (lastMACD > lastSignal) return "Yükseliş (MACD > Sinyal)";
            if (lastMACD < lastSignal) return "Düşüş (MACD < Sinyal)";
            return "Yatay (MACD = Sinyal)";
        }
    }
    return "Durum Belirlenemedi";
}

// WebSocket setup to Binance kline stream (lowercase symbol required)
const wsUrl = `wss://stream.binance.com:9443/ws/${SYMBOL_BINANCE.toLowerCase()}@kline_${CFG.INTERVAL}`;
let ws = null;

function startWebsocket() {
    ws = new WebSocket(wsUrl);

    ws.on('open', () => {
        console.log('✅ WebSocket bağlantısı açıldı:', wsUrl);
    });

    ws.on('message', async (message) => {
        try {
            const data = JSON.parse(message);
            const k = data.k;
            // k: { t: startTime, T: closeTime, o, h, l, c, x: isFinal, ... }
            if (!k) return;

            const isFinal = k.x === true || k.isFinal === true;
            const closeTime = Number(k.T ?? k.closeTime ?? k.close_time);
            const open = Number(k.o);
            const high = Number(k.h);
            const low = Number(k.l);
            const close = Number(k.c);

            // Only process closed bars and only if newer than lastKnownCloseTime
            if (isFinal && closeTime > lastKnownCloseTime) {
                // process
                const result = await macdBotStrategy.processCandle(closeTime, open, high, low, close);
                lastKnownCloseTime = closeTime; // update after processing
                const signal = result.signal;

                console.log(`Yeni kapanış barı: ${new Date(closeTime).toISOString()} close=${close} signal=${signal?.type || 'none'}`);

                if (signal?.type === 'CONFIRMED') {
                    if (signal.signalType === 'BUY') {
                        await placeOrder('BUY', signal.message);
                    } else if (signal.signalType === 'SELL') {
                        await placeOrder('SELL', signal.message);
                    }
                } else if (signal?.type === 'REJECTED') {
                    sendTelegramMessage(signal.message);
                }
            }
        } catch (err) {
            console.error('WebSocket message işlenirken hata:', err);
        }
    });

    ws.on('close', (code, reason) => {
        console.warn(`❌ WebSocket kapandı (${code}) ${reason}. 5s sonra yeniden bağlanıyor...`);
        setTimeout(() => startWebsocket(), 5000);
    });

    ws.on('error', (err) => {
        console.error('WebSocket hatası:', err.message || err);
    });
}

// =========================================================================================
// STARTUP
// =========================================================================================
(async () => {
    await fetchInitialData();
    // start websocket only if not simulation; in sim mode user might prefer polling mocks
    if (!isSimulationMode) {
        startWebsocket();
    } else {
        console.log('Simülasyon modunda websocket başlatılmadı.');
    }
})();

// =========================================================================================
// EXPRESS
// =========================================================================================
app.get('/', (req, res) => {
    res.send('Bot çalışıyor!');
});

app.listen(PORT, () => {
    console.log(`Sunucu http://localhost:${PORT} adresinde çalışıyor`);
});
