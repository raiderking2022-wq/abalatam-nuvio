'use strict';
const server = require('../server');
module.exports = (req, res) => {
  req.url = '/manifest.json';
  server.emit('request', req, res);
};
