// index.js
import fs from 'fs';
import WebSocket from 'ws';
import dotenv from 'dotenv';
import express from 'express';
import fetch from 'node-fetch';
import pkg from 'binance-api-node';
import { TvDatafeed, Interval } from 'tvdatafeed';

const Binance = pkg.default || pkg;
dotenv.config();

const app = express();
const PORT = process.env.PORT || 3000;

// ========================================
// MACD BOT STRATEJİ SINIFI
// ========================================
class MACDBotStrategy {
    constructor(options = {}) {
        this.shortPeriod = options.shortPeriod || 12;
        this.longPeriod = options.longPeriod || 26;
        this.signalPeriod = options.signalPeriod || 9;
        this.trendAnalysisBars = options.trendAnalysisBars || 50;
        this.klines = [];
        this.macdLine = [];
        this.signalLine = [];
    }

    calculateEMA(prices, period) {
        const k = 2 / (period + 1);
        let ema = [];
        let sum = 0;
        for (let i = 0; i < prices.length; i++) {
            if (i < period - 1) {
                sum += prices[i];
                ema.push(null);
            } else if (i === period - 1) {
                sum += prices[i];
                ema.push(sum / period);
            } else {
                const prevEma = ema[i - 1];
                ema.push(prices[i] * k + prevEma * (1 - k));
            }
        }
        return ema.filter(e => e !== null);
    }

    calculateMACD(data) {
        if (data.length < this.longPeriod) return;
        const prices = data.map(d => d.close);
        const shortEMA = this.calculateEMA(prices, this.shortPeriod);
        const longEMA = this.calculateEMA(prices, this.longPeriod);
        const macdLine = longEMA.map((long, i) => shortEMA[i + (this.longPeriod - this.shortPeriod)] - long);
        if (macdLine.length < this.signalPeriod) return;
        const signalLine = this.calculateEMA(macdLine, this.signalPeriod);
        this.macdLine = macdLine;
        this.signalLine = signalLine;
    }

    async getTrendAnalysis() {
        if (this.klines.length < this.trendAnalysisBars) return "Unknown";
        const trendData = this.klines.slice(-this.trendAnalysisBars).map(k => k.close);
        const apiKey = process.env.GEMINI_API_KEY;
        const apiUrl = `https://generativelanguage.googleapis.com/v1beta/models/gemini-2.5-flash-preview-05-20:generateContent?key=${apiKey}`;
        const prompt = `Analyze the following financial data points and determine if the general trend is 'Bullish', 'Bearish', or 'Sideways'. Only respond with a single word: Bullish, Bearish, or Sideways. Data points: ${trendData.join(', ')}`;
        const payload = { contents: [{ parts: [{ text: prompt }] }] };

        try {
            const response = await fetch(apiUrl, {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify(payload)
            });
            if (!response.ok) return "Unknown";
            const result = await response.json();
            const trend = result.candidates?.[0]?.content?.parts?.[0]?.text?.trim() || 'Unknown';
            return trend;
        } catch (error) {
            console.error("Error during Gemini API call:", error);
            return "Unknown";
        }
    }

    async processCandle(timestamp, open, high, low, close) {
        this.klines.push({ timestamp, open, high, low, close });
        if (this.klines.length > 500) this.klines.shift();
        this.calculateMACD(this.klines);

        if (this.macdLine.length < 2 || this.signalLine.length < 2) return { signal: null };

        const lastMACD = this.macdLine[this.macdLine.length - 1];
        const prevMACD = this.macdLine[this.macdLine.length - 2];
        const lastSignal = this.signalLine[this.signalLine.length - 1];
        const prevSignal = this.signalLine[this.signalLine.length - 2];

        let signalType = null, trend = null, message = null;

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
            if (trend.toLowerCase() === 'sideways') {
                return { signal: { type: 'REJECTED', signalType, message: `Sinyal Reddedildi: Piyasa yatay. TrendAI trendi '${trend}'.` } };
            } else {
                return { signal: { type: 'CONFIRMED', signalType, message } };
            }
        }
        return { signal: null };
    }
}

// ========================================
// CONFIG
// ========================================
const CFG = {
    SYMBOL: process.env.SYMBOL || 'ETHUSDT',
    INTERVAL: process.env.INTERVAL || '1m',
    TG_TOKEN: process.env.TG_TOKEN,
    TG_CHAT_ID: process.env.TG_CHAT_ID,
    IS_TESTNET: process.env.IS_TESTNET === 'true'
};

// GLOBAL STATE
let botCurrentPosition = 'none';
let botEntryPrice = 0;
let totalNetProfit = 0;
let isBotInitialized = false;

// Binance client
const isSimulationMode = !process.env.BINANCE_API_KEY || !process.env.BINANCE_SECRET_KEY;
const binanceClient = isSimulationMode ? {
    candles: async ({ symbol, interval, limit }) => {
        const mock = [];
        let price = 4300;
        let now = Date.now();
        for (let i = 0; i < limit; i++) {
            const open = price;
            const close = open + (Math.random() - 0.5) * 10;
            mock.push({ open, high: Math.max(open, close), low: Math.min(open, close), close, closeTime: now - (limit - i) * 60000, volume: 1000 });
            price = close;
        }
        return mock;
    }
} : Binance({ apiKey: process.env.BINANCE_API_KEY, apiSecret: process.env.BINANCE_SECRET_KEY, test: CFG.IS_TESTNET });

// MACD BOT
const macdBotStrategy = new MACDBotStrategy({
    shortPeriod: parseInt(process.env.MACD_SHORT_PERIOD, 10) || 12,
    longPeriod: parseInt(process.env.MACD_LONG_PERIOD, 10) || 26,
    signalPeriod: parseInt(process.env.MACD_SIGNAL_PERIOD, 10) || 9,
    trendAnalysisBars: parseInt(process.env.TREND_ANALYSIS_BARS, 10) || 50
});

// ========================================
// TELEGRAM
// ========================================
async function sendTelegramMessage(text) {
    if (!CFG.TG_TOKEN || !CFG.TG_CHAT_ID) return;
    const telegramApiUrl = `https://api.telegram.org/bot${CFG.TG_TOKEN}/sendMessage`;
    await fetch(telegramApiUrl, { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ chat_id: CFG.TG_CHAT_ID, text, parse_mode: 'Markdown' }) });
}

// ========================================
// BINANCE GEÇMİŞ VERİLER
// ========================================
async function fetchInitialData() {
    const initialKlines = await binanceClient.candles({ symbol: CFG.SYMBOL, interval: CFG.INTERVAL, limit: 500 });
    initialKlines.forEach(k => macdBotStrategy.klines.push({ timestamp: k.closeTime, open: k.open, high: k.high, low: k.low, close: k.close }));
    macdBotStrategy.calculateMACD(macdBotStrategy.klines);
    isBotInitialized = true;
}
await fetchInitialData();

// ========================================
// TRADINGVIEW CANLI VERİLER
// ========================================
const tv = new TvDatafeed();
async function listenTVBars() {
    const symbol = CFG.SYMBOL.replace('USDT', 'USD'); // TV sembol uyarlaması
    setInterval(async () => {
        try {
            const bars = await tv.get_hist(symbol, Interval.in_1_minute, 1);
            if (!bars.length) return;
            const bar = bars[bars.length - 1];
            const result = await macdBotStrategy.processCandle(Date.now(), bar.open, bar.high, bar.low, bar.close);
            const signal = result.signal;
            if (signal?.type === 'CONFIRMED') await placeOrder(signal.signalType, signal.message);
            else if (signal?.type === 'REJECTED') sendTelegramMessage(signal.message);
        } catch (err) { console.error('TV fetch error:', err); }
    }, 60 * 1000);
}
listenTVBars();

// ========================================
// ORDER PLACEMENT
// ========================================
async function placeOrder(side, signalMessage) {
    const lastClose = macdBotStrategy.klines[macdBotStrategy.klines.length - 1]?.close || 0;
    botCurrentPosition = side.toLowerCase();
    botEntryPrice = lastClose;
    sendTelegramMessage(`🚀 ${side} emri gerçekleşti. Fiyat: ${lastClose}\n${signalMessage}`);
}

// ========================================
// EXPRESS
// ========================================
app.get('/', (req, res) => res.send('Bot çalışıyor!'));
app.listen(PORT, () => console.log(`Sunucu http://localhost:${PORT} adresinde çalışıyor`));
