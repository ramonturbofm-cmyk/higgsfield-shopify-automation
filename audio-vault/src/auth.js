const crypto = require('crypto');

// Several databases can run on one PC (localhost:3000, :3001, ...). Browsers share
// cookies between ports, so each database gets its own cookie name.
const SESSION_COOKIE = /^[A-Za-z0-9_]{1,64}$/.test(process.env.SESSION_COOKIE || '') ? process.env.SESSION_COOKIE : 'av_session';
const SESSION_DAYS = 14;

function hashPassword(password) {
  const salt = crypto.randomBytes(16);
  const hash = crypto.scryptSync(password, salt, 64);
  return `scrypt$${salt.toString('hex')}$${hash.toString('hex')}`;
}

function verifyPassword(password, stored) {
  if (!stored) return false;
  const [scheme, saltHex, hashHex] = stored.split('$');
  if (scheme !== 'scrypt') return false;
  const expected = Buffer.from(hashHex, 'hex');
  const actual = crypto.scryptSync(password, Buffer.from(saltHex, 'hex'), expected.length);
  return crypto.timingSafeEqual(expected, actual);
}

const randomToken = () => crypto.randomBytes(24).toString('base64url');
const sha256 = (value) => crypto.createHash('sha256').update(value).digest('hex');

// sid = the login (one device) this cookie belongs to, so a login can be ended on its
// own and the number of devices per account can be limited.
const SID = /^[A-Za-z0-9_-]{16,64}$/;
function signSession(secret, userId, sessionVersion, sid) {
  const expires = Date.now() + SESSION_DAYS * 24 * 3600 * 1000;
  const payload = sid ? `${userId}.${sessionVersion}.${expires}.${sid}` : `${userId}.${sessionVersion}.${expires}`;
  const sig = crypto.createHmac('sha256', secret).update(payload).digest('base64url');
  return `${payload}.${sig}`;
}

function readSession(secret, value) {
  if (!value) return null;
  const parts = value.split('.');
  // 4 parts: a cookie from before devices were tracked (no sid); 5 parts: with sid.
  if (parts.length !== 4 && parts.length !== 5) return null;
  const sig = parts.pop();
  const [userId, sessionVersion, expires, sid = null] = parts;
  if (sid !== null && !SID.test(sid)) return null;
  const expected = crypto.createHmac('sha256', secret).update(parts.join('.')).digest('base64url');
  if (sig.length !== expected.length || !crypto.timingSafeEqual(Buffer.from(sig), Buffer.from(expected))) return null;
  if (Number(expires) < Date.now()) return null;
  return { userId: Number(userId), sessionVersion: Number(sessionVersion), expires: Number(expires), sid };
}

function parseCookies(header = '') {
  const out = {};
  for (const part of header.split(';')) {
    const i = part.indexOf('=');
    if (i > 0) out[part.slice(0, i).trim()] = decodeURIComponent(part.slice(i + 1).trim());
  }
  return out;
}

module.exports = {
  SESSION_COOKIE, SESSION_DAYS, hashPassword, verifyPassword, randomToken, sha256,
  signSession, readSession, parseCookies,
};
