'use strict';
const handler = require('../server');
module.exports = (req, res) => {
  req.url = '/manifest.json';
  return handler(req, res);
};
