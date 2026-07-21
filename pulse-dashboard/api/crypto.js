// Vercel Serverless Function — CoinGecko proxy
// Centralises crypto fetches server-side and caches them at the edge so all
// visitors share one upstream call every ~20s (avoids per-visitor rate limits).
//
//   GET /api/crypto[?ids=bitcoin,ethereum,...]

const DEFAULT_IDS = 'bitcoin,ethereum,solana,binancecoin,ripple,cardano,dogecoin,tron';
const ALLOWED = /^[a-z0-9,\-]{1,300}$/;

export default async function handler(req, res) {
  res.setHeader('Access-Control-Allow-Origin', '*');
  res.setHeader('Access-Control-Allow-Methods', 'GET, OPTIONS');
  if (req.method === 'OPTIONS') { res.status(204).end(); return; }

  const ids = (req.query.ids || DEFAULT_IDS).toString();
  if (!ALLOWED.test(ids)) {
    res.status(400).json({ error: 'invalid ids' });
    return;
  }

  const url = 'https://api.coingecko.com/api/v3/coins/markets'
            + `?vs_currency=usd&ids=${encodeURIComponent(ids)}`
            + '&order=market_cap_desc&sparkline=true&price_change_percentage=24h';

  try {
    const headers = { 'Accept': 'application/json' };
    // Optional: set COINGECKO_API_KEY in Vercel env for higher limits
    if (process.env.COINGECKO_API_KEY) {
      headers['x-cg-demo-api-key'] = process.env.COINGECKO_API_KEY;
    }
    const upstream = await fetch(url, { headers });
    if (!upstream.ok) {
      res.status(upstream.status).json({ error: `upstream ${upstream.status}` });
      return;
    }
    const data = await upstream.json();
    res.setHeader('Cache-Control', 's-maxage=20, stale-while-revalidate=60');
    res.status(200).json(data);
  } catch (e) {
    res.status(502).json({ error: 'upstream fetch failed' });
  }
}
