'use strict';
const handler = require('../server');
module.exports = (req, res) => {
  const u = new URL(req.url, 'http://localhost');
  const route = u.searchParams.get('route') || u.pathname.replace(/^\/addon\/?/, '');
  u.searchParams.delete('route');
  req.url = '/' + route.replace(/^\/+/, '') + (u.searchParams.size ? '?' + u.searchParams.toString() : '');
  return handler(req, res);
};
