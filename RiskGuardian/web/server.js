// ============================================
// x-trade.ai — Local Dashboard Server
// Zero dependencies, pure Node.js
// ============================================

const http = require('http');
const fs = require('fs');
const path = require('path');
const os = require('os');

const PORT = 3777;
const DATA_DIR = path.join(os.homedir(), 'AppData', 'Roaming', 'xtrade-ai');
const WEB_DIR = __dirname;

// MIME types
const MIME = {
  '.html': 'text/html',
  '.css': 'text/css',
  '.js': 'application/javascript',
  '.json': 'application/json',
  '.png': 'image/png',
  '.svg': 'image/svg+xml',
};

// Get today's journal file
function getJournalPath() {
  const d = new Date();
  const date = d.toISOString().split('T')[0];
  return path.join(DATA_DIR, `journal_${date}.csv`);
}

function getLockPath() {
  const d = new Date();
  const date = d.toISOString().split('T')[0];
  return path.join(DATA_DIR, `lock_${date}.json`);
}

// Parse CSV to JSON
function parseJournal() {
  const fp = getJournalPath();
  if (!fs.existsSync(fp)) return { entries: [], lock: null };

  const text = fs.readFileSync(fp, 'utf-8');
  const lines = text.trim().split('\n');
  if (lines.length < 2) return { entries: [], lock: null };

  const entries = [];
  for (let i = 1; i < lines.length; i++) {
    const c = lines[i].split(',');
    if (c.length < 11) continue;
    entries.push({
      time: c[0],
      event: c[1],
      direction: c[2],
      price: parseFloat(c[3]) || 0,
      volume: parseFloat(c[4]) || 0,
      pnl: parseFloat(c[5]) || 0,
      closedPnl: parseFloat(c[6]) || 0,
      totalPnl: parseFloat(c[7]) || 0,
      trades: parseInt(c[8]) || 0,
      consecLosses: parseInt(c[9]) || 0,
      reason: c.slice(10).join(',')
    });
  }

  // Read lock file
  let lock = null;
  const lp = getLockPath();
  if (fs.existsSync(lp)) {
    try { lock = JSON.parse(fs.readFileSync(lp, 'utf-8')); } catch {}
  }

  return { entries, lock };
}

// SSE clients
const sseClients = new Set();

// Watch journal file for changes
let lastSize = 0;
function watchJournal() {
  const fp = getJournalPath();
  if (!fs.existsSync(fp)) return;

  try {
    const stat = fs.statSync(fp);
    if (stat.size !== lastSize) {
      lastSize = stat.size;
      const data = parseJournal();
      const msg = `data: ${JSON.stringify(data)}\n\n`;
      for (const client of sseClients) {
        try { client.write(msg); } catch { sseClients.delete(client); }
      }
    }
  } catch {}
}

setInterval(watchJournal, 1000);

// HTTP Server
const server = http.createServer((req, res) => {
  const url = new URL(req.url, `http://localhost:${PORT}`);

  // CORS
  res.setHeader('Access-Control-Allow-Origin', '*');

  // API: Get journal data
  if (url.pathname === '/api/journal') {
    const data = parseJournal();
    res.writeHead(200, { 'Content-Type': 'application/json' });
    res.end(JSON.stringify(data));
    return;
  }

  // SSE: Real-time updates
  if (url.pathname === '/api/stream') {
    res.writeHead(200, {
      'Content-Type': 'text/event-stream',
      'Cache-Control': 'no-cache',
      'Connection': 'keep-alive'
    });
    sseClients.add(res);

    // Send initial data
    const data = parseJournal();
    res.write(`data: ${JSON.stringify(data)}\n\n`);

    req.on('close', () => sseClients.delete(res));
    return;
  }

  // API: List available journal dates
  if (url.pathname === '/api/dates') {
    if (!fs.existsSync(DATA_DIR)) {
      res.writeHead(200, { 'Content-Type': 'application/json' });
      res.end('[]');
      return;
    }
    const files = fs.readdirSync(DATA_DIR)
      .filter(f => f.startsWith('journal_') && f.endsWith('.csv'))
      .map(f => f.replace('journal_', '').replace('.csv', ''))
      .sort().reverse();
    res.writeHead(200, { 'Content-Type': 'application/json' });
    res.end(JSON.stringify(files));
    return;
  }

  // API: Get specific date
  if (url.pathname.startsWith('/api/journal/')) {
    const date = url.pathname.split('/').pop();
    const fp = path.join(DATA_DIR, `journal_${date}.csv`);
    if (!fs.existsSync(fp)) {
      res.writeHead(404); res.end('Not found'); return;
    }
    const text = fs.readFileSync(fp, 'utf-8');
    // Parse same as above
    const lines = text.trim().split('\n');
    const entries = [];
    for (let i = 1; i < lines.length; i++) {
      const c = lines[i].split(',');
      if (c.length < 11) continue;
      entries.push({
        time: c[0], event: c[1], direction: c[2],
        price: parseFloat(c[3]) || 0, volume: parseFloat(c[4]) || 0,
        pnl: parseFloat(c[5]) || 0, closedPnl: parseFloat(c[6]) || 0,
        totalPnl: parseFloat(c[7]) || 0, trades: parseInt(c[8]) || 0,
        consecLosses: parseInt(c[9]) || 0, reason: c.slice(10).join(',')
      });
    }
    res.writeHead(200, { 'Content-Type': 'application/json' });
    res.end(JSON.stringify({ entries, lock: null }));
    return;
  }

  // Static files
  let filePath = url.pathname === '/' ? '/dashboard.html' : url.pathname;
  filePath = path.join(WEB_DIR, filePath);

  const ext = path.extname(filePath);
  const contentType = MIME[ext] || 'application/octet-stream';

  fs.readFile(filePath, (err, data) => {
    if (err) {
      res.writeHead(404);
      res.end('Not found');
      return;
    }
    res.writeHead(200, { 'Content-Type': contentType });
    res.end(data);
  });
});

server.listen(PORT, () => {
  console.log('');
  console.log('  ╔══════════════════════════════════════╗');
  console.log('  ║     x-trade.ai Dashboard Server      ║');
  console.log('  ╠══════════════════════════════════════╣');
  console.log(`  ║  http://localhost:${PORT}              ║`);
  console.log('  ║  Real-time journal streaming active   ║');
  console.log('  ╚══════════════════════════════════════╝');
  console.log('');
  console.log(`  Watching: ${DATA_DIR}`);
  console.log('  Press Ctrl+C to stop');
  console.log('');
});
