/* AbaLatam 1.1.0 - Proveedor nativo para Nuvio (películas y series).
 * Sin eval, sin claves incrustadas, sin dependencias de Node.
 * Úsalo solo con contenido que tengas derecho a ver.
 */
'use strict';
var NAME = 'AbaLatam';
var BASE = 'https://pelis182.net';
var UA = 'Mozilla/5.0 (Linux; Android 13) AppleWebKit/537.36 Chrome/130.0.0.0 Mobile Safari/537.36';
var TIMEOUT_MS = 9000;

function log(msg) { try { console.log('[AbaLatam] ' + msg); } catch (e) {} }

function withTimeout(promise, ms) {
  return new Promise(function (resolve, reject) {
    var done = false;
    var t = setTimeout(function () { if (!done) { done = true; reject(new Error('timeout')); } }, ms);
    promise.then(function (v) { if (!done) { done = true; clearTimeout(t); resolve(v); } },
      function (e) { if (!done) { done = true; clearTimeout(t); reject(e); } });
  });
}

function get(url, referer, ua) {
  var headers = { 'User-Agent': ua || UA, 'Accept-Language': 'es-ES,es;q=0.9' };
  if (referer) headers.Referer = referer;
  return withTimeout(fetch(url, { headers: headers }).then(function (r) {
    if (!r.ok) throw new Error('http_' + r.status);
    return r.text();
  }), TIMEOUT_MS);
}

function decode(s) {
  return String(s || '').replace(/&quot;/g, '"').replace(/&#0?38;/g, '&').replace(/&amp;/g, '&')
    .replace(/&#8211;/g, '–').replace(/&#8217;/g, "'").replace(/&#(\d+);/g, function (_, n) {
      return String.fromCharCode(+n);
    }).replace(/&lt;/g, '<').replace(/&gt;/g, '>');
}

// Normaliza títulos: sin acentos, sin signos, minúsculas.
function norm(s) {
  return decode(s).toLowerCase().normalize('NFD').replace(/[\u0300-\u036f]/g, '')
    .replace(/&/g, ' y ').replace(/[^a-z0-9]+/g, ' ').replace(/^(el|la|los|las|the) /, '').trim();
}

/* ---------- Metadatos (sin clave): TMDB -> Wikidata ---------- */
function metadata(tmdbId, type) {
  var id = String(tmdbId).replace(/^tmdb:/, '');
  var prop, imdbMode = /^tt\d+$/.test(id);
  var where;
  if (imdbMode) where = '?item wdt:P345 "' + id + '".';
  else {
    if (!/^\d+$/.test(id)) return Promise.reject(new Error('invalid_id'));
    prop = type === 'tv' ? 'P4983' : 'P4947';
    where = '?item wdt:' + prop + ' "' + id + '".';
  }
  var q = 'SELECT ?es ?en ?mx ?mul ?orig ?date WHERE {' + where +
    ' OPTIONAL{?item rdfs:label ?mul FILTER(lang(?mul)="mul")}' +
    ' OPTIONAL{?item wdt:P1476 ?orig}' +
    ' OPTIONAL{?item rdfs:label ?es FILTER(lang(?es)="es")}' +
    ' OPTIONAL{?item rdfs:label ?mx FILTER(lang(?mx)="es-419")}' +
    ' OPTIONAL{?item rdfs:label ?en FILTER(lang(?en)="en")}' +
    ' OPTIONAL{?item wdt:P577 ?date}} LIMIT 1';
  return get('https://query.wikidata.org/sparql?format=json&query=' + encodeURIComponent(q))
    .then(function (t) {
      var b = (JSON.parse(t).results.bindings || [])[0] || {};
      var titles = [b.mx, b.es, b.en, b.mul, b.orig].filter(Boolean).map(function (x) { return x.value; });
      var year = b.date ? parseInt(b.date.value.slice(0, 4), 10) : 0;
      if (!titles.length) throw new Error('metadata_not_found');
      return { titles: titles.filter(function (v, i, a) { return a.indexOf(v) === i; }), year: year };
    });
}

function metadataTmdb(tmdbId, type) {
  var key = typeof TMDB_API_KEY !== 'undefined' ? TMDB_API_KEY : '';
  var id = String(tmdbId).replace(/^tmdb:/, '');
  if (!key || !/^\d+$/.test(id)) return metadata(tmdbId, type);
  return get('https://api.themoviedb.org/3/' + type + '/' + id + '?language=es-MX&api_key=' + encodeURIComponent(key))
    .then(function (t) {
      var m = JSON.parse(t);
      var titles = [m.title || m.name, m.original_title || m.original_name].filter(Boolean);
      return { titles: titles, year: parseInt((m.release_date || m.first_air_date || '0').slice(0, 4), 10) };
    }).then(function (a) {
      return metadata(tmdbId, type).then(function (b) {
        a.titles = a.titles.concat(b.titles).filter(function (v, i, x) { return x.indexOf(v) === i; });
        return a;
      }, function () { return a; });
    }, function () { return metadata(tmdbId, type); });
}

// Título latino (es-MX) desde la ficha pública de TMDB, sin clave.
function metadataLatino(tmdbId, type) {
  var id = String(tmdbId).replace(/^tmdb:/, '');
  var fallback = function () { return metadataTmdb(tmdbId, type); };
  if (!/^\d+$/.test(id)) return fallback();
  return get('https://www.themoviedb.org/' + type + '/' + id + '?language=es-MX', null, 'okhttp/4.12.0').then(function (html) {
    var t = /<meta property="og:title" content="([^"]+)"/.exec(html);
    var y = /<title>[^<]*\((?:[^)]*?)(\d{4})\)/.exec(html);
    if (!t) return fallback();
    var a = { titles: [decode(t[1])], year: y ? +y[1] : 0 };
    return fallback().then(function (b) {
      a.titles = a.titles.concat(b.titles).filter(function (v, i, x) { return x.indexOf(v) === i; });
      if (!a.year) a.year = b.year;
      return a;
    }, function () { return a; });
  }, fallback);
}

/* ---------- Buscador de la web ---------- */
function parseCards(html) {
  var out = [], re = /<article class="p182-card">([\s\S]*?)<\/article>/g, m;
  while ((m = re.exec(html))) {
    var href = /href="([^"]+)"/.exec(m[1]);
    var h3 = /<h3>([\s\S]*?)<\/h3>/.exec(m[1]);
    var meta = /<div class="p182-card__meta"><span>(\d{4})/.exec(m[1]);
    if (href && h3) out.push({
      url: decode(href[1]), title: decode(h3[1]).trim(),
      year: meta ? +meta[1] : 0, serie: /p182-card__badge">\s*SERIE/i.test(m[1])
    });
  }
  return out;
}

function search(query, tipo) {
  return get(BASE + '/?s=' + encodeURIComponent(query) + '&tipo=' + tipo, BASE + '/')
    .then(parseCards).catch(function (e) { log('busqueda: ' + e.message); return []; });
}

// Quita "– Temporada N" del título de la web.
function splitSeason(title) {
  var m = /^(.*?)\s*[–-]\s*Temporada\s+(\d+)\s*$/i.exec(title);
  return m ? { name: m[1], season: +m[2] } : { name: title, season: 0 };
}

function findPage(meta, type, season) {
  var tipo = type === 'tv' ? 'series' : 'peliculas';
  var wanted = meta.titles.map(norm);
  var queries = [];
  meta.titles.forEach(function (t) {
    queries.push(t);
    var short = t.split(/[:–-]/)[0].trim();
    if (short.length > 2) queries.push(short);
  });
  queries = queries.filter(function (v, i, a) { return a.indexOf(v) === i; }).slice(0, 6);

  function score(card) {
    var info = splitSeason(card.title);
    var n = norm(info.name);
    var s = 0;
    if (wanted.indexOf(n) >= 0) s = 100;
    else if (wanted.some(function (w) { return n.indexOf(w) === 0 || w.indexOf(n) === 0; })) s = 60;
    else return -1;
    if (type === 'tv') {
      if (!card.serie && !info.season) return -1;
      if (info.season === season) s += 50; else if (info.season) return -1;
    } else {
      if (card.serie) return -1;
      if (meta.year && card.year) {
        var d = Math.abs(meta.year - card.year);
        if (d === 0) s += 30; else if (d === 1) s += 15; else s -= 40;
      }
    }
    return s;
  }

  var i = 0;
  function next() {
    if (i >= queries.length) return Promise.resolve(null);
    return search(queries[i++], tipo).then(function (cards) {
      var best = null, bs = 0;
      cards.forEach(function (c) { var s = score(c); if (s > bs) { bs = s; best = c; } });
      return best && bs >= 60 ? best : next();
    });
  }
  return next();
}

/* ---------- Reproductores ---------- */
function playersFromPage(html, type, season, episode) {
  var urls = [];
  if (type === 'tv') {
    // Botones de episodio: data-iframe="<iframe src=...>" ... >T6X1</button>
    var re = /<button[^>]*data-iframe="([^"]*)"[^>]*>\s*T(\d+)\s*X\s*(\d+)\s*<\/button>/gi, m;
    while ((m = re.exec(html))) {
      if (+m[3] === +episode && (+m[2] === +season || !season)) {
        var src = /src="([^"]+)"/.exec(decode(m[1]));
        if (src) urls.push(src[1]);
      }
    }
    return urls;
  }
  var re2 = /https?:\/\/(?:www\.)?paulinito\.com\/player\/[A-Za-z0-9_-]+\/?/g, x;
  var clean = html.replace(/\\\//g, '/');
  while ((x = re2.exec(clean))) if (urls.indexOf(x[0]) < 0) urls.push(x[0]);
  return urls.slice(0, 3);
}

function resolvePaulinito(url, referer) {
  return get(url, referer).then(function (html) {
    html = html.replace(/\\\//g, '/');
    var out = [];
    var block = /sources\s*:\s*(\[[\s\S]*?\])/.exec(html);
    if (block) {
      try {
        JSON.parse(block[1]).forEach(function (s) {
          if (s.file) out.push({ url: s.file, label: s.label || 'HD' });
        });
      } catch (e) {}
    }
    if (!out.length) {
      var m = /https?:\/\/[^\s"'<>]+?\.(?:m3u8|mp4)(?:\?[^\s"'<>]*)?/i.exec(html);
      if (m) out.push({ url: m[0], label: 'HD' });
    }
    // Cada fuente se convierte en la lista de calidades que realmente ofrece.
    var headers = { 'User-Agent': UA, 'Referer': url, 'Origin': 'https://paulinito.com' };
    var list = out.map(function (s) {
      return masterQualities(s.url, headers, s.label);
    });
    return Promise.all(list).then(function (groups) { return [].concat.apply([], groups); });
  }).catch(function (e) { log('reproductor: ' + e.message); return []; });
}

// Lee la lista maestra y crea un enlace por calidad (360p a 1080p).
function masterQualities(masterUrl, headers, label) {
  return get(masterUrl, headers.Referer).then(function (text) {
    var base = masterUrl.replace(/[^/]*$/, '');
    var entries = [], re = /#EXT-X-STREAM-INF:[^\n]*RESOLUTION=(\d+)x(\d+)[^\n]*\n([^\s]+)/g, m;
    while ((m = re.exec(text))) {
      var src = m[3];
      if (!/^https?:/.test(src)) src = base + src.replace(/^\/+/, '');
      entries.push({
        name: NAME,
        title: NAME + ' · Latino · ' + m[2] + 'p',
        url: src,
        quality: m[2] + 'p',
        headers: headers
      });
    }
    if (!entries.length) {
      // Lista simple: una sola calidad.
      var single = /1080/.test(label || '') ? '1080p' : /720|HD/i.test(label || '') ? '720p' : 'Auto';
      entries.push({
        name: NAME, title: NAME + ' · Latino · ' + (label || single),
        url: masterUrl, quality: single, headers: headers
      });
    }
    return entries;
  }).catch(function (e) {
    log('lista de calidades: ' + e.message);
    return [{
      name: NAME, title: NAME + ' · Latino · ' + (label || 'Auto'),
      url: masterUrl, quality: /1080/.test(label || '') ? '1080p' : 'Auto', headers: headers
    }];
  });
}

/* ---------- Entrada de Nuvio ---------- */
function getStreams(tmdbId, mediaType, season, episode) {
  var type = mediaType === 'tv' || mediaType === 'series' ? 'tv' : 'movie';
  season = parseInt(season, 10) || 1;
  episode = parseInt(episode, 10) || 1;
  return metadataLatino(tmdbId, type).then(function (meta) {
    log('titulos: ' + meta.titles.join(' | '));
    return findPage(meta, type, season);
  }).then(function (card) {
    if (!card) { log('sin coincidencia'); return []; }
    return get(card.url, BASE + '/').then(function (html) {
      var players = playersFromPage(html, type, season, episode);
      return Promise.all(players.map(function (p) { return resolvePaulinito(p, card.url); }))
        .then(function (lists) { return [].concat.apply([], lists); });
    });
  }).catch(function (e) { log('error: ' + e.message); return []; });
}

if (typeof module !== 'undefined' && module.exports) module.exports = { getStreams: getStreams };
