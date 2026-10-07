// Read-only WebDAV endpoint so mAirList (or Windows Explorer, macOS Finder, ...)
// can mount the library as a network drive. Login: e-mail + personal API token.
const express = require('express');
const path = require('path');
const { sha256 } = require('./auth');
const { isActive, canDownload, listCollections, collectionAccess, readableFile, fileName, fileIdFromName, logAccess } = require('./library');

const esc = (s) => String(s).replace(/[<>&'"]/g, (c) => ({ '<': '&lt;', '>': '&gt;', '&': '&amp;', "'": '&apos;', '"': '&quot;' }[c]));
const href = (...segments) => '/dav/' + segments.map(encodeURIComponent).join('/');

function entry({ href: h, name, isDir, size, mime, created }) {
  const date = new Date(created || Date.now());
  return `<D:response><D:href>${esc(h)}</D:href><D:propstat><D:prop>`
    + `<D:displayname>${esc(name)}</D:displayname>`
    + (isDir ? '<D:resourcetype><D:collection/></D:resourcetype>'
      : `<D:resourcetype/><D:getcontentlength>${size}</D:getcontentlength><D:getcontenttype>${esc(mime)}</D:getcontenttype>`)
    + `<D:creationdate>${date.toISOString()}</D:creationdate><D:getlastmodified>${date.toUTCString()}</D:getlastmodified>`
    + '</D:prop><D:status>HTTP/1.1 200 OK</D:status></D:propstat></D:response>';
}

function multistatus(res, entries) {
  res.status(207).type('application/xml; charset=utf-8')
    .send(`<?xml version="1.0" encoding="utf-8"?><D:multistatus xmlns:D="DAV:">${entries.join('')}</D:multistatus>`);
}

function createDavRouter({ pool, filesDir }) {
  const router = express.Router();

  router.use(async (req, res, next) => {
    const header = req.headers.authorization || '';
    const decoded = header.startsWith('Basic ') ? Buffer.from(header.slice(6), 'base64').toString() : '';
    const token = decoded.slice(decoded.indexOf(':') + 1);
    if (token) {
      const { rows } = await pool.query('SELECT * FROM users WHERE api_token_hash = $1 AND NOT disabled', [sha256(token)]);
      if (rows.length && isActive(rows[0]) && canDownload(rows[0])) { req.user = rows[0]; return next(); }
    }
    res.set('WWW-Authenticate', 'Basic realm="Audio OnAir Turbo Database", charset="UTF-8"').status(401).send('Login met je e-mail en API-token');
  });

  router.use(async (req, res, next) => {
    try {
      res.set({ DAV: '1', 'MS-Author-Via': 'DAV' });
      if (req.method === 'OPTIONS') return res.set('Allow', 'OPTIONS, PROPFIND, GET, HEAD').status(200).end();
      if (!['PROPFIND', 'GET', 'HEAD'].includes(req.method)) return res.status(405).set('Allow', 'OPTIONS, PROPFIND, GET, HEAD').end();

      const segments = req.path.split('/').filter(Boolean).map(decodeURIComponent);
      const depth = req.headers.depth === '0' ? 0 : 1;
      const collections = await listCollections(pool, req.user);

      if (segments.length === 0) {
        if (req.method !== 'PROPFIND') return res.type('text').send('Audio OnAir Turbo Database WebDAV');
        const out = [entry({ href: '/dav/', name: 'Audio OnAir Turbo Database', isDir: true })];
        if (depth) for (const c of collections) out.push(entry({ href: href(c.name) + '/', name: c.name, isDir: true, created: c.created_at }));
        return multistatus(res, out);
      }

      const collection = collections.find((c) => c.name === segments[0]);
      if (!collection || segments.length > 2) return res.status(404).end();

      if (segments.length === 1) {
        if (req.method !== 'PROPFIND') return res.status(405).end();
        const out = [entry({ href: href(collection.name) + '/', name: collection.name, isDir: true, created: collection.created_at })];
        if (depth) {
          const { rows } = await pool.query('SELECT * FROM audio_files WHERE collection_id = $1 ORDER BY artist, title', [collection.id]);
          for (const f of rows) {
            out.push(entry({ href: href(collection.name, fileName(f)), name: fileName(f), size: f.size_bytes, mime: f.mime_type, created: f.created_at }));
          }
        }
        return multistatus(res, out);
      }

      const file = await readableFile(pool, req.user, fileIdFromName(segments[1]));
      if (!file || file.collection_id !== collection.id || !(await collectionAccess(pool, req.user, collection.id)).read) {
        return res.status(404).end();
      }
      if (req.method === 'PROPFIND') {
        return multistatus(res, [entry({ href: href(collection.name, fileName(file)), name: fileName(file), size: file.size_bytes, mime: file.mime_type, created: file.created_at })]);
      }
      if (req.method === 'GET' && !/^bytes=(?!0-)/.test(req.headers.range || '')) {
        logAccess(pool, req.user.id, file.id, 'webdav', req.headers['user-agent']);
      }
      res.type(file.mime_type);
      return res.sendFile(path.join(filesDir, file.storage_key));
    } catch (err) {
      next(err);
    }
  });

  return router;
}

module.exports = { createDavRouter };
