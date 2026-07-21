// Vercel Serverless Function — Yahoo Finance proxy
// Fetches the Yahoo chart endpoint server-side so the browser never needs
// a public CORS proxy. Cached at the edge to stay well within rate limits.
//
//   GET /api/yahoo?symbol=^NSEI[&range=1d&interval=5m]

const ALLOWED = /^[A-Za-z0-9.^=\-]{1,20}$/; // simple symbol allowlist

export default async function handler(req, res) {
  res.setHeader('Access-Control-Allow-Origin', '*');
  res.setHeader('Access-Control-Allow-Methods', 'GET, OPTIONS');
  if (req.method === 'OPTIONS') { res.status(204).end(); return; }

  const symbol = (req.query.symbol || '').toString();
  const range = (req.query.range || '1d').toString();
  const interval = (req.query.interval || '5m').toString();

  if (!ALLOWED.test(symbol)) {
    res.status(400).json({ error: 'invalid or missing symbol' });
    return;
  }

  const url = `https://query1.finance.yahoo.com/v8/finance/chart/${encodeURIComponent(symbol)}`
            + `?range=${encodeURIComponent(range)}&interval=${encodeURIComponent(interval)}`;

  try {
    const upstream = await fetch(url, {
      headers: {
        'User-Agent': 'Mozilla/5.0 (compatible; PulseDashboard/1.0)',
        'Accept': 'application/json',
      },
    });
    if (!upstream.ok) {
      res.status(upstream.status).json({ error: `upstream ${upstream.status}` });
      return;
    }
    const data = await upstream.json();
    // Edge cache: 1 fresh fetch per ~15s serves every visitor
    res.setHeader('Cache-Control', 's-maxage=15, stale-while-revalidate=45');
    res.status(200).json(data);
  } catch (e) {
    res.status(502).json({ error: 'upstream fetch failed' });
  }
}
