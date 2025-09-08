// Gerekli bağımlılıkları içe aktarın
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

        // Dahili durum
        this.klines = [];
        this.macdLine = [];
        this.signalLine = [];
    }

    // Üstel Hareketli Ortalama (EMA) hesaplama
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
    
    // MACD ve Sinyal Hattı hesaplama
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

    async processCandle(timestamp, open, high, low, close) {
        const existingBarIndex = this.klines.findIndex(k => k.closeTime === timestamp);
        if (existingBarIndex !== -1) {
            // Mum zaten mevcut, sadece güncelle
            this.klines[existingBarIndex] = { timestamp, open, high, low, close };
        } else {
            // Yeni mum ekle
            this.klines.push({ timestamp, open, high, low, close });
            if (this.klines.length > 500) {
                this.klines.shift();
            }
        }
        
        this.calculateMACD(this.klines);

        if (this.macdLine.length < 2 || this.signalLine.length < 2) {
            return { signal: null };
        }

        const lastMACD = this.macdLine[this.macdLine.length - 1];
        const prevMACD = this.macdLine[this.macdLine.length - 2];
        const lastSignal = this.signalLine[this.signalLine.length - 1];
        const prevSignal = this.signalLine[this.signalLine.length - 2];

        let signalType = null;
        let trend = null;
        let message = null;

        // Alış sinyali kontrolü
        if (prevMACD < prevSignal && lastMACD > lastSignal) {
            signalType = 'BUY';
            trend = await this.getTrendAnalysis();
            message = `MACD Sinyali: AL - TrendAI tarafından onaylandı: ${trend}`;
        }
        // Satış sinyali kontrolü
        else if (prevMACD > prevSignal && lastMACD < lastSignal) {
            signalType = 'SELL';
            trend = await this.getTrendAnalysis();
            message = `MACD Sinyali: SAT - TrendAI tarafından onaylandı: ${trend}`;
        }

        if (signalType) {
            if (trend.toLowerCase() === 'sideways') {
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
// TRADINGVIEW API ENTEGRASYONU
// =========================================================================================

// TradingView'in genel API'sini kullanarak veri çekeceğiz
const TRADINGVIEW_API_URL = 'https://api.tradingview.com/data/v1/history';

async function fetchTradingViewData(symbol, interval, limit) {
    try {
        const response = await fetch(`${TRADINGVIEW_API_URL}?symbol=${symbol}&interval=${interval}&limit=${limit}`);
        if (!response.ok) {
            throw new Error(`TradingView API error: ${response.status} ${response.statusText}`);
        }
        const data = await response.json();
        return data.bars.map(bar => ({
            timestamp: bar.time,
            open: bar.open,
            high: bar.high,
            low: bar.low,
            close: bar.close
        }));
    } catch (error) {
        console.error('TradingView verilerini çekerken hata:', error);
        return [];
    }
}

// =========================================================================================
// STRATEGY CONFIGURATION
// =========================================================================================
const CFG = {
    SYMBOL: process.env.SYMBOL || 'BINANCE:ETHUSDT',
    INTERVAL: process.env.INTERVAL || '1', // TradingView için '1', '5', '15', '60' vb.
    TG_TOKEN: process.env.TG_TOKEN,
    TG_CHAT_ID: process.env.TG_CHAT_ID,
    IS_TESTNET: process.env.IS_TESTNET === 'true',
};

// =========================================================================================
// GLOBAL STATE
// =========================================================================================
let botCurrentPosition = 'none';
let botEntryPrice = 0;
let totalNetProfit = 0;
let isBotInitialized = false;
let lastKnownCloseTime = 0;

const isSimulationMode = !process.env.BINANCE_API_KEY || !process.env.BINANCE_SECRET_KEY;

// Mock Binance Client for simulation mode
const mockBinanceClient = {
    futuresAccountBalance: async () => [{ asset: 'USDT', availableBalance: '1000' }],
    futuresMarketOrder: async ({ side, quantity }) => {
        console.log(`[SİMÜLASYON] ${side} emri başarıyla oluşturuldu: ${quantity}`);
        return { status: 'FILLED' };
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

    // Önceki pozisyonu kapat
    if (botCurrentPosition !== 'none' && botCurrentPosition !== side.toLowerCase()) {
        try {
            const pnl = botCurrentPosition === 'long' ? (lastClosePrice - botEntryPrice) : (botEntryPrice - lastClosePrice);
            totalNetProfit += pnl;

            const profitMessage = pnl >= 0 ? `+${pnl.toFixed(2)} USDT` : `${pnl.toFixed(2)} USDT`;
            const positionCloseMessage = `📉 Pozisyon kapatıldı! ${botCurrentPosition.toUpperCase()}\n\nSon Kapanış Fiyatı: ${lastClosePrice}\nBu İşlemden Kâr/Zarar: ${profitMessage}\n**Toplam Net Kâr: ${totalNetProfit.toFixed(2)} USDT**`;
            sendTelegramMessage(positionCloseMessage);

            console.log(`[SİMÜLASYON] Mevcut pozisyon (${botCurrentPosition}) kapatıldı.`);
            botCurrentPosition = 'none';
        } catch (error) {
            console.error('Mevcut pozisyonu kapatırken hata oluştu:', error);
            return;
        }
    }

    // Yeni pozisyonu aç
    if (botCurrentPosition === 'none') {
        try {
            const currentPrice = lastClosePrice;
            let quantity = (100 * (100 / 100)) / currentPrice;
            
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
// DATA HANDLING
// =========================================================================================

// MACD durumunu almak için yardımcı fonksiyon
function getMACDStatus(macdLine, signalLine) {
    if (macdLine.length < 1 || signalLine.length < 1) {
        return "Durum Belirlenemedi";
    }
    const lastMACD = macdLine[macdLine.length - 1];
    const lastSignal = signalLine[signalLine.length - 1];
    
    if (lastMACD > lastSignal) {
        return "Yükseliş (MACD > Sinyal)";
    } else if (lastMACD < lastSignal) {
        return "Düşüş (MACD < Sinyal)";
    } else {
        return "Yatay (MACD = Sinyal)";
    }
}

async function fetchInitialData() {
    try {
        const initialBars = await fetchTradingViewData(CFG.SYMBOL, CFG.INTERVAL, 500);

        initialBars.forEach(k => {
            macdBotStrategy.klines.push({
                timestamp: k.timestamp,
                open: k.open,
                high: k.high,
                low: k.low,
                close: k.close
            });
        });
        
        lastKnownCloseTime = macdBotStrategy.klines[macdBotStrategy.klines.length - 1]?.timestamp || 0;

        console.log(`✅ İlk ${macdBotStrategy.klines.length} mum verisi yüklendi.`);

        if (!isBotInitialized) {
            macdBotStrategy.calculateMACD(macdBotStrategy.klines);
            const macdStatus = getMACDStatus(macdBotStrategy.macdLine, macdBotStrategy.signalLine);
            
            const initialMessage = `✅ Bot başlatıldı!\n\n**Mod:** ${isSimulationMode ? 'Simülasyon' : 'Canlı İşlem'}\n**Veri Kaynağı:** TradingView\n**Sembol:** ${CFG.SYMBOL}\n**Zaman Aralığı:** ${CFG.INTERVAL} dk\n\n**MACD'nin Şu Anki Durumu:** ${macdStatus}`;
            sendTelegramMessage(initialMessage);
            isBotInitialized = true;
        }

    } catch (error) {
        console.error('İlk verileri çekerken hata:', error);
    }
}

// Yeni verileri düzenli aralıklarla kontrol etme
const pollingInterval = parseInt(CFG.INTERVAL, 10) * 60 * 1000; // Saniye cinsinden
setInterval(async () => {
    try {
        const latestBars = await fetchTradingViewData(CFG.SYMBOL, CFG.INTERVAL, 5);
        if (latestBars.length > 0) {
            const newBars = latestBars.filter(bar => bar.timestamp > lastKnownCloseTime);
            
            for (const newBar of newBars) {
                const result = await macdBotStrategy.processCandle(newBar.timestamp, newBar.open, newBar.high, newBar.low, newBar.close);
                const signal = result.signal;
                
                console.log(`Yeni mum verisi geldi. Fiyat: ${newBar.close}. Sinyal: ${signal?.type || 'none'}.`);

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
            if (newBars.length > 0) {
                lastKnownCloseTime = newBars[newBars.length - 1].timestamp;
            }
        }
    } catch (error) {
        console.error('Veri çekerken hata:', error);
    }
}, pollingInterval);

fetchInitialData();

app.get('/', (req, res) => {
    res.send('Bot çalışıyor!');
});

app.listen(PORT, () => {
    console.log(`Sunucu http://localhost:${PORT} adresinde çalışıyor`);
});
