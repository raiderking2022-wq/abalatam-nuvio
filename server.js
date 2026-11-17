/* AbaLatam 1.1.0 · addon para Nuvio (protocolo Stremio).
 * Sirve manifiesto, catálogos y metadatos de la web pública.
 * No aloja video: los enlaces los resuelve el plugin en el dispositivo.
 */
'use strict';
const http = require('node:http');
const fs = require('node:fs');
const path = require('node:path');
const BASE = 'https://pelis182.net';
const UA = 'Mozilla/5.0 (Linux; Android 13) AppleWebKit/537.36 Chrome/130.0.0.0 Mobile Safari/537.36';
const VERSION = '1.3.1';

async function fetchText(url) {
  const r = await fetch(url, { headers: { 'User-Agent': UA, 'Accept-Language': 'es' }, signal: AbortSignal.timeout(9000) });
  if (!r.ok) throw new Error('http_' + r.status);
  return r.text();
}
function decode(s) {
  return String(s || '').replace(/&quot;/g, '"').replace(/&#0?38;/g, '&').replace(/&amp;/g, '&')
    .replace(/&#8211;/g, '–').replace(/&#(\d+);/g, (_, n) => String.fromCharCode(+n)).trim();
}
function cards(html) {
  const out = [], re = /<article class="p182-card">([\s\S]*?)<\/article>/g; let m;
  while ((m = re.exec(html))) {
    const href = /href="([^"]+)"/.exec(m[1]);
    const h3 = /<h3>([\s\S]*?)<\/h3>/.exec(m[1]);
    const img = /src="(https:\/\/image\.tmdb\.org[^"]+)"/.exec(m[1]);
    const meta = /<div class="p182-card__meta"><span>(\d{4})<\/span><span>([\d.]+)<\/span>/.exec(m[1]);
    const serie = /p182-card__badge">\s*SERIE/i.test(m[1]);
    if (href && h3) {
      const slug = decode(href[1]).split('/').filter(Boolean).pop();
      out.push({
        id: 'aba:' + slug,
        type: serie ? 'series' : 'movie',
        name: decode(h3[1]),
        poster: img ? img[1] : undefined,
        releaseInfo: meta ? meta[1] : undefined,
        imdbRating: meta ? meta[2] : undefined
      });
    }
  }
  return out;
}
async function catalog(type, search, skip) {
  const tipo = type === 'series' ? 'series' : 'peliculas';
  let url;
  if (search) url = BASE + '/?s=' + encodeURIComponent(search) + '&tipo=' + tipo;
  else if (type === 'series') url = BASE + '/category/series/' + (skip ? '?paged=' + (Math.floor(skip / 28) + 1) : '');
  else url = BASE + '/catalogo/' + (skip ? '?paged=' + (Math.floor(skip / 28) + 1) : '');
  const html = await fetchText(url);
  return { metas: cards(html).filter(c => c.type === type) };
}
// Funciones del plugin (las mismas que usa Nuvio en el dispositivo).
const { getStreams, _internal: plugin } = require('./providers/abalatam.js');

async function meta(type, id) {
  const slug = id.replace(/^aba:/, '');
  const html = await fetchText(BASE + '/' + slug + '/');
  const og = /<meta property="og:image" content="([^"]+)"/.exec(html);
  const desc = /<meta name="description" content="([^"]*)"/.exec(html);
  const h1 = /<h1 class="watch-title">([^<]+)<\/h1>/.exec(html);
  const year = /"dateCreated":"(\d{4})/.exec(html);
  const m = {
    id, type,
    name: h1 ? decode(h1[1]) : slug,
    poster: og ? og[1] : undefined, background: og ? og[1] : undefined,
    description: desc ? decode(desc[1]) : undefined,
    releaseInfo: year ? year[1] : undefined
  };
  if (type === 'series') {
    // Episodios a partir de los botones T{temporada}X{episodio} de la página.
    const seen = new Set(), videos = [];
    const re = /<button[^>]*data-iframe="[^"]*"[^>]*>\s*T(\d+)\s*X\s*(\d+)\s*<\/button>/gi;
    let e;
    while ((e = re.exec(html))) {
      const vid = id + ':' + (+e[1]) + ':' + (+e[2]);
      if (seen.has(vid)) continue;
      seen.add(vid);
      videos.push({ id: vid, title: 'Episodio ' + (+e[2]), season: +e[1], episode: +e[2], released: new Date(0).toISOString() });
    }
    videos.sort((a, b) => a.season - b.season || a.episode - b.episode);
    m.videos = videos;
  }
  return { meta: m };
}

function toStream(s) {
  return {
    name: s.name, title: s.title, url: s.url,
    headers: s.headers,
    behaviorHints: { notWebReady: true, bingeGroup: 'abalatam-' + s.quality, proxyHeaders: { request: s.headers } }
  };
}

async function streams(type, id) {
  if (!id.startsWith('aba:')) {
    // IDs de IMDb (tt123 / tt123:1:2) o TMDB (tmdb:123 / tmdb:123:1:2): búsqueda del plugin.
    const p = id.split(':');
    const base = p[0] === 'tmdb' ? p[1] : p[0];
    const rest = p[0] === 'tmdb' ? p.slice(2) : p.slice(1);
    const list = await getStreams(base, type === 'series' ? 'tv' : 'movie', rest[0], rest[1]);
    console.log('[addon] stream ' + id + ': ' + list.length + ' fuentes');
    return { streams: list.map(toStream) };
  }
  const parts = id.replace(/^aba:/, '').split(':');
  const slug = parts[0];
  const kind = type === 'series' ? 'tv' : 'movie';
  const season = +parts[1] || 1, episode = +parts[2] || 1;
  const pageUrl = BASE + '/' + slug + '/';
  const html = await fetchText(pageUrl);
  const players = plugin.playersFromPage(html, kind, season, episode);
  console.log('[addon] stream ' + id + ': ' + players.length + ' reproductores');
  const lists = await Promise.all(players.map(p => plugin.resolvePaulinito(p, pageUrl)));
  return { streams: plugin.sortByQuality([].concat(...lists)).map(toStream) };
}

async function handler(req, res) {
  const send = (code, data) => {
    if (res.headersSent) return res.end();
    res.writeHead(code, { 'Content-Type': 'application/json', 'Access-Control-Allow-Origin': '*' });
    res.end(JSON.stringify(data));
  };
  try {
    const u = new URL(req.url, 'http://x');
    const origin = (process.env.PUBLIC_BASE_URL || 'https://' + (req.headers.host || 'localhost')).replace(/\/$/, '');
    if (u.pathname === '/manifest.json') {
      return send(200, {
        id: 'org.abalatam.catalog', version: VERSION, name: 'AbaLatam',
        description: 'Películas y series en español latino: catálogo, episodios y fuentes.',
        logo: origin + '/logo.png', background: origin + '/banner.jpg',
        types: ['movie', 'series'],
        resources: [
          { name: 'catalog', types: ['movie', 'series'] },
          { name: 'meta', types: ['movie', 'series'], idPrefixes: ['aba:'] },
          { name: 'stream', types: ['movie', 'series'], idPrefixes: ['aba:', 'tt', 'tmdb:'] }
        ],
        catalogs: [
          { type: 'movie', id: 'aba-movies', name: 'AbaLatam · Películas', extra: [{ name: 'search', isRequired: false }, { name: 'skip', isRequired: false }] },
          { type: 'series', id: 'aba-series', name: 'AbaLatam · Series', extra: [{ name: 'search', isRequired: false }, { name: 'skip', isRequired: false }] }
        ],
        behaviorHints: { configurable: false, configurationRequired: false }
      });
    }
    let m;
    if ((m = /^\/catalog\/(movie|series)\/[\w-]+\.json$/.exec(u.pathname)) ||
        (m = /^\/catalog\/(movie|series)\/[\w-]+\/search=(.+)\.json$/.exec(u.pathname))) {
      return send(200, await catalog(m[1], m[2] ? decodeURIComponent(m[2]) : null, +u.searchParams.get('skip') || 0));
    }
    if ((m = /^\/meta\/(movie|series)\/([^/]+)\.json$/.exec(u.pathname))) {
      return send(200, await meta(m[1], decodeURIComponent(m[2])));
    }
    if ((m = /^\/stream\/(movie|series)\/([^/]+)\.json$/.exec(u.pathname))) {
      return send(200, await streams(m[1], decodeURIComponent(m[2])));
    }
    if (u.pathname === '/logo.png' || u.pathname === '/banner.jpg') {
      const file = path.join(__dirname, u.pathname === '/logo.png' ? 'logo.png' : 'banner.jpg');
      const img = fs.readFileSync(file);
      res.writeHead(200, {
        'Content-Type': u.pathname.endsWith('.png') ? 'image/png' : 'image/jpeg',
        'Access-Control-Allow-Origin': '*', 'Cache-Control': 'public, max-age=3600'
      });
      return res.end(img);
    }
    send(404, { error: 'not_found' });
  } catch (e) {
    console.log('[addon] ' + req.url + ' -> ' + (e && e.message));
    const p = (req.url || '').split('/')[1];
    send(200, p === 'stream' ? { streams: [] } : p === 'meta' ? { meta: null } : { metas: [] });
  }
}
if (require.main === module) http.createServer(handler).listen(process.env.PORT || 3000);
module.exports = handler;
