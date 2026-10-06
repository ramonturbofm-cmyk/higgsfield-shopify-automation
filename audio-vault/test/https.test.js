const test = require('node:test');
const assert = require('node:assert');
const fs = require('fs');
const path = require('path');
const { parseDomain, duckdnsName } = require('../desktop/server-manager');

test('internetadres: opschonen en controleren', () => {
  assert.equal(parseDomain(''), '');
  assert.equal(parseDomain('  TurboFM.DuckDNS.org '), 'turbofm.duckdns.org');
  assert.equal(parseDomain('https://radio.turbofm.nl/studio.html'), 'radio.turbofm.nl');
  for (const bad of ['localhost', 'turbofm', '192.168.1.20', 'turbo fm.nl', 'x.duckdns.org;rm', '-a.nl']) {
    assert.throws(() => parseDomain(bad), /geen geldig internetadres/, bad);
  }
});

test('DuckDNS-naam alleen voor .duckdns.org', () => {
  assert.equal(duckdnsName('turbofm.duckdns.org'), 'turbofm');
  assert.equal(duckdnsName('radio.turbofm.nl'), '');
  assert.equal(duckdnsName('a.b.duckdns.org'), '');
});

test('installatiepakket bevat alle compose-bestanden die de app gebruikt', () => {
  const pkg = JSON.parse(fs.readFileSync(path.join(__dirname, '../desktop/package.json'), 'utf8'));
  const bundled = pkg.build.extraResources[0].filter;
  const used = fs.readFileSync(path.join(__dirname, '../desktop/server-manager.js'), 'utf8').match(/docker-compose[\w.-]*\.yml/g);
  for (const file of new Set(used)) assert.ok(bundled.includes(file), `${file} ontbreekt in extraResources`);
  assert.ok(bundled.includes('caddy/**'));
  assert.ok(fs.existsSync(path.join(__dirname, '../caddy/Caddyfile')));
});
