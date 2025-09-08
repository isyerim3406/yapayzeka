// Gerekli bağımlılıkları içe aktarın
import WebSocket from 'ws';
import dotenv from 'dotenv';
import express from 'express';
import fetch from 'node-fetch';
import pkg from 'binance-api-node';
const Binance = pkg.default || pkg;

dotenv.config();

const app = express();
const PORT = process.env.PORT || 3000;

// =========================================================================================
// MACD BOT STRATEJİ SINIFI
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
        this.pos = 0; // 1 = long, -1 = short, 0 = no position
    }

    // Üstel Hareketli Ortalama (EMA) hesaplama
    calculateEMA(prices, period) {
        if (prices.length < period) return [];
        const k = 2 / (period + 1);
        let ema = [prices[0]];
        for (let i = 1; i < prices.length; i++) {
            ema.push(prices[i] * k + ema[i - 1] * (1 - k));
        }
        return ema;
    }

    // MACD ve Sinyal Hattı hesaplama
    calculateMACD(data) {
        if (data.length < this.longPeriod + this.signalPeriod) return;
        const prices = data.map(d => d.close);
        const shortEMA = this.calculateEMA(prices, this.shortPeriod);
        const longEMA = this.calculateEMA(prices, this.longPeriod);

        const macdLine = shortEMA.slice(this.longPeriod - this.shortPeriod).map((ema, i) => ema - longEMA[i]);
        const signalLine = this.calculateEMA(macdLine, this.signalPeriod);

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

    async processCandle(timestamp, open, high, low, close) {
        this.klines.push({ timestamp, open, high, low, close });
        if (this.klines.length > 500) {
            this.klines.shift();
        }

        this.calculateMACD(this.klines);

        if (this.macdLine.length < 2 || this.signalLine.length < 2) {
            return { signal: null };
        }

        const lastMACD = this.macdLine[this.macdLine.length - 1];
        const prevMACD = this.macdLine[this.macdLine.length - 2];
        const lastSignal = this.signalLine[this.signalLine.length - 1];
        const prevSignal = this.signalLine[this.signalLine.length - 2];

        const prevPos = this.pos;

        if (prevMACD < prevSignal && lastMACD > lastSignal) {
            this.pos = 1; // Long signal
        } else if (prevMACD > prevSignal && lastMACD < lastSignal) {
            this.pos = -1; // Short signal
        }

        let signal = null;
        if (this.pos !== prevPos) {
            const trend = await this.getTrendAnalysis();
            const isValidSignal = (this.pos === 1 && trend.toLowerCase() === 'bullish') ||
                                  (this.pos === -1 && trend.toLowerCase() === 'bearish');
            if (isValidSignal) {
                signal = {
                    type: this.pos === 1 ? 'BUY' : 'SELL',
                    message: `MACD Sinyali: ${this.pos === 1 ? 'AL' : 'SAT'} - TrendAI tarafından onaylandı: ${trend}`
                };
            } else {
                signal = {
                    type: 'REJECTED',
                    message: `Sinyal Reddedildi: MACD ${this.pos === 1 ? 'AL' : 'SAT'} sinyali verdi ancak TrendAI trendi '${trend}' olarak belirledi.`
                };
                this.pos = prevPos; // Revert position
            }
        }
        
        return { signal };
    }
}

// =========================================================================================
// STRATEGY CONFIGURATION
// =========================================================================================
const CFG = {
    SYMBOL: process.env.SYMBOL || 'ETHUSDT',
    INTERVAL: process.env.INTERVAL || '1m',
    TG_TOKEN: process.env.TG_TOKEN,
    TG_CHAT_ID: process.env.TG_CHAT_ID,
    IS_TESTNET: process.env.IS_TESTNET === 'true',
};

// =========================================================================================
// GLOBAL STATE
// =========================================================================================
let botCurrentPosition = 'none';
let totalNetProfit = 0;
let isBotInitialized = false;

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

const macdBotStrategy = new MACDBotStrategy();

// =========================================================================================
// TELEGRAM
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
// ORDER PLACEMENT & TRADING LOGIC
// =========================================================================================
async function placeOrder(side, signalMessage) {
    const lastClosePrice = macdBotStrategy.klines[macdBotStrategy.klines.length - 1]?.close || 0;

    // Check if we need to close an existing position first
    if (botCurrentPosition !== 'none' && botCurrentPosition !== side.toLowerCase()) {
        try {
            // Placeholder for profit calculation
            const profit = botCurrentPosition === 'long' ? (lastClosePrice - 4300) : (4300 - lastClosePrice); 
            totalNetProfit += profit;
            
            const profitMessage = profit >= 0 ? `+${profit.toFixed(2)} USDT` : `${profit.toFixed(2)} USDT`;
            const positionCloseMessage = `📉 Pozisyon kapatıldı! ${botCurrentPosition.toUpperCase()}\n\nSon Kapanış Fiyatı: ${lastClosePrice}\nBu İşlemden Kâr/Zarar: ${profitMessage}\n**Toplam Net Kâr: ${totalNetProfit.toFixed(2)} USDT**`;
            sendTelegramMessage(positionCloseMessage);

            // In a real bot, you'd place a closing order here.
            console.log(`[SİMÜLASYON] Mevcut pozisyon (${botCurrentPosition}) kapatıldı.`);
        } catch (error) {
            console.error('Mevcut pozisyonu kapatırken hata oluştu:', error);
            return;
        }
    }

    if (botCurrentPosition === 'none' || botCurrentPosition !== side.toLowerCase()) {
        try {
            const currentPrice = lastClosePrice;
            let quantity = 0;
            
            // In a real bot, you'd calculate quantity based on balance.
            quantity = (100 * (100 / 100)) / currentPrice; // Using a fixed capital for simulation
            
            // In a real bot, you'd place a market order here.
            console.log(`[SİMÜLASYON] ${side} emri verildi. Fiyat: ${currentPrice}`);
            
            botCurrentPosition = side.toLowerCase();
            const positionMessage = `🚀 **${side} Emri Gerçekleşti!**\n\n**Sinyal:** ${signalMessage}\n**Fiyat:** ${currentPrice}\n**Miktar:** ${quantity.toFixed(4)}\n**Toplam Net Kâr: ${totalNetProfit.toFixed(2)} USDT**`;
            sendTelegramMessage(positionMessage);
        } catch (error) {
            console.error('Emir verirken hata oluştu:', error);
        }
    }
}

// =========================================================================================
// DATA HANDLING
// =========================================================================================
const ws = new WebSocket(`wss://stream.binance.com:9443/ws/${CFG.SYMBOL.toLowerCase()}@kline_${CFG.INTERVAL}`);

async function fetchInitialData() {
    try {
        const initialKlines = await binanceClient.candles({
            symbol: CFG.SYMBOL,
            interval: CFG.INTERVAL,
            limit: 500
        });

        initialKlines.forEach(k => {
            macdBotStrategy.klines.push({
                timestamp: k.closeTime,
                open: parseFloat(k.open),
                high: parseFloat(k.high),
                low: parseFloat(k.low),
                close: parseFloat(k.close)
            });
        });

        console.log(`✅ İlk ${macdBotStrategy.klines.length} mum verisi yüklendi.`);

        if (!isBotInitialized) {
            sendTelegramMessage(`✅ Bot başlatıldı!\n\n**Mod:** ${isSimulationMode ? 'Simülasyon' : 'Canlı İşlem'}\n**Sembol:** ${CFG.SYMBOL}\n**Zaman Aralığı:** ${CFG.INTERVAL}`);
            isBotInitialized = true;
        }

    } catch (error) {
        console.error('İlk verileri çekerken hata:', error);
    }
}

fetchInitialData();

ws.on('message', async (message) => {
    const data = JSON.parse(message);
    const klineData = data.k;

    if (klineData.x) { // Check if candle is closed
        const newBar = {
            open: parseFloat(klineData.o),
            high: parseFloat(klineData.h),
            low: parseFloat(klineData.l),
            close: parseFloat(klineData.c),
            closeTime: klineData.T
        };
        
        const result = await macdBotStrategy.processCandle(newBar.closeTime, newBar.open, newBar.high, newBar.low, newBar.close);
        const signal = result.signal;
        
        console.log(`Yeni mum verisi geldi. Fiyat: ${newBar.close}. Sinyal: ${signal?.type || 'none'}.`);

        if (signal?.type === 'BUY' && botCurrentPosition !== 'long') {
            await placeOrder('BUY', signal.message);
        } else if (signal?.type === 'SELL' && botCurrentPosition !== 'short') {
            await placeOrder('SELL', signal.message);
        }
    }
});

ws.on('close', () => {
    console.log('❌ WebSocket bağlantısı kapandı. Yeniden bağlanıyor...');
    // Real-world application would include a reconnection logic here
});

ws.on('error', (error) => {
    console.error('WebSocket hatası:', error.message);
});

app.get('/', (req, res) => {
    res.send('Bot çalışıyor!');
});

app.listen(PORT, () => {
    console.log(`Sunucu http://localhost:${PORT} adresinde çalışıyor`);
});
