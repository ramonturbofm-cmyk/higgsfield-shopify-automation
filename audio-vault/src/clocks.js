// Hour clocks: CRUD, the weekly schedule, and the planner that turns a clock into
// a list of files using rotation rules (no song repeats within the rotation window,
// no artist twice within a few songs, least recently played first).
const express = require('express');
const lib = require('./library');

const SLOT_TYPES = new Set(['muziek', 'jingle', 'vast']);
const ROTATION_HOURS = Number(process.env.ROTATION_HOURS || 3);
const ARTIST_SEPARATION = 4;

const HEX = /^#[0-9a-f]{6}$/i;

function cleanSlots(slots) {
  if (!Array.isArray(slots) || slots.length > 200) throw Object.assign(new Error('Ongeldige slots'), { status: 400 });
  return slots.map((s) => {
    if (!s || !SLOT_TYPES.has(s.type)) throw Object.assign(new Error('Onbekend slot-type'), { status: 400 });
    const out = { type: s.type };
    if (s.type === 'vast') out.file_id = Number(s.file_id) || null;
    else out.collection_id = Number(s.collection_id) || null;
    if (HEX.test(s.color || '')) out.color = s.color;
    return out;
  });
}

function pickRandom(list) {
  return list[Math.floor(Math.random() * list.length)];
}

function createClockRouter({ pool, requireUser, requireAdmin, wrap, HttpError }) {
  const router = express.Router();

  router.get('/clocks', requireUser, wrap(async (req, res) => {
    const { rows: clocks } = await pool.query('SELECT id, name, color, slots FROM clocks ORDER BY name');
    const { rows: schedule } = await pool.query('SELECT day, hour, clock_id FROM clock_schedule');
    res.json({ clocks, schedule });
  }));

  function clockBody(body) {
    const name = String((body && body.name) || '').trim().slice(0, 80);
    if (!name) throw new HttpError(400, 'Geef de klok een naam');
    return { name, color: HEX.test(body.color || '') ? body.color : '#2f7bff', slots: cleanSlots(body.slots || []) };
  }

  const unique = (fn) => async (...args) => {
    try { return await fn(...args); } catch (err) {
      if (err.code === '23505') throw new HttpError(409, 'Er bestaat al een klok met deze naam');
      throw err;
    }
  };

  router.post('/clocks', requireUser, requireAdmin, wrap(unique(async (req, res) => {
    const c = clockBody(req.body);
    const { rows } = await pool.query('INSERT INTO clocks (name, color, slots) VALUES ($1, $2, $3) RETURNING id, name, color, slots',
      [c.name, c.color, JSON.stringify(c.slots)]);
    res.status(201).json({ clock: rows[0] });
  })));

  router.put('/clocks/:id', requireUser, requireAdmin, wrap(unique(async (req, res) => {
    const c = clockBody(req.body);
    const { rows } = await pool.query('UPDATE clocks SET name = $1, color = $2, slots = $3 WHERE id = $4 RETURNING id, name, color, slots',
      [c.name, c.color, JSON.stringify(c.slots), Number(req.params.id)]);
    if (!rows.length) throw new HttpError(404, 'Klok niet gevonden');
    res.json({ clock: rows[0] });
  })));

  router.delete('/clocks/:id', requireUser, requireAdmin, wrap(async (req, res) => {
    await pool.query('DELETE FROM clocks WHERE id = $1', [Number(req.params.id)]);
    res.json({ ok: true });
  }));

  // Replace the whole week: cells = [{ day, hour, clock_id }]
  router.put('/clock-schedule', requireUser, requireAdmin, wrap(async (req, res) => {
    const cells = Array.isArray(req.body && req.body.cells) ? req.body.cells : null;
    if (!cells || cells.length > 168) throw new HttpError(400, 'cells moet een lijst van maximaal 168 uren zijn');
    const client = await pool.connect();
    try {
      await client.query('BEGIN');
      await client.query('DELETE FROM clock_schedule');
      for (const c of cells) {
        await client.query('INSERT INTO clock_schedule (day, hour, clock_id) VALUES ($1, $2, $3) ON CONFLICT DO NOTHING',
          [Number(c.day), Number(c.hour), Number(c.clock_id)]);
      }
      await client.query('COMMIT');
    } catch (err) {
      await client.query('ROLLBACK');
      if (err.code === '23503' || err.code === '23514') throw new HttpError(400, 'Ongeldige planning');
      throw err;
    } finally {
      client.release();
    }
    res.json({ ok: true });
  }));

  // Plan one or more hours. The studio sends the local day/hour (so time zones
  // never matter) and the files already waiting in its playlist.
  //   { hours: [{ day, hour }], exclude: [fileId, ...] }
  router.post('/clocks/plan', requireUser, wrap(async (req, res) => {
    const hours = Array.isArray(req.body && req.body.hours) ? req.body.hours.slice(0, 24) : [];
    if (!hours.length) throw new HttpError(400, 'Geef minstens één uur op');
    const readable = new Set((await lib.listCollections(pool, req.user)).map((c) => c.id));
    const { rows: files } = await pool.query(
      `SELECT f.id, f.collection_id, f.title, f.artist,
              (SELECT max(created_at) FROM access_log l WHERE l.file_id = f.id AND l.action = 'onair') AS last_played
         FROM audio_files f WHERE f.collection_id = ANY($1)`, [[...readable]]);
    const byId = new Map(files.map((f) => [f.id, f]));
    const byCollection = new Map();
    for (const f of files) {
      if (!byCollection.has(f.collection_id)) byCollection.set(f.collection_id, []);
      byCollection.get(f.collection_id).push(f);
    }

    // Recently played or already queued files are off limits; recent artists too.
    const { rows: recent } = await pool.query(
      `SELECT l.file_id FROM access_log l WHERE l.action = 'onair' AND l.created_at > now() - make_interval(hours => $1)
        ORDER BY l.created_at`, [ROTATION_HOURS]);
    const used = new Set([...recent.map((r) => r.file_id), ...(req.body.exclude || []).map(Number)]);
    const recentArtists = [...recent.map((r) => byId.get(r.file_id)), ...(req.body.exclude || []).map((id) => byId.get(Number(id)))]
      .filter(Boolean).map((f) => f.artist.toLowerCase()).filter(Boolean).slice(-ARTIST_SEPARATION);

    const { rows: clocks } = await pool.query('SELECT id, name, color, slots FROM clocks');
    const clockById = new Map(clocks.map((c) => [c.id, c]));
    const { rows: schedule } = await pool.query('SELECT day, hour, clock_id FROM clock_schedule');
    const scheduled = new Map(schedule.map((s) => [`${s.day}:${s.hour}`, s.clock_id]));

    function choose(slot) {
      if (slot.type === 'vast') {
        const f = byId.get(slot.file_id);
        return f || null;
      }
      const options = byCollection.get(slot.collection_id) || [];
      if (!options.length) return null;
      const artistOk = (f) => slot.type !== 'muziek' || !f.artist || !recentArtists.includes(f.artist.toLowerCase());
      // Relax the rules step by step rather than leaving a hole in the hour.
      const candidates = [
        options.filter((f) => !used.has(f.id) && artistOk(f)),
        options.filter((f) => !used.has(f.id)),
        options,
      ].find((l) => l.length);
      // Least recently played third, then random within that, so it doesn't feel mechanical.
      candidates.sort((a, b) => (a.last_played ? new Date(a.last_played).getTime() : 0) - (b.last_played ? new Date(b.last_played).getTime() : 0));
      return pickRandom(candidates.slice(0, Math.max(1, Math.ceil(candidates.length / 3))));
    }

    const planned = hours.map(({ day, hour }) => {
      const clock = clockById.get(scheduled.get(`${Number(day)}:${Number(hour)}`));
      if (!clock) return { day, hour, clock: null, items: [], missing: 0 };
      const items = [];
      let missing = 0;
      for (const slot of clock.slots) {
        const f = choose(slot);
        if (!f) { missing++; continue; }
        items.push(f.id);
        used.add(f.id);
        if (slot.type === 'muziek' && f.artist) {
          recentArtists.push(f.artist.toLowerCase());
          if (recentArtists.length > ARTIST_SEPARATION) recentArtists.shift();
        }
      }
      return { day, hour, clock: { id: clock.id, name: clock.name, color: clock.color }, items, missing };
    });
    res.json({ planned });
  }));

  return router;
}

module.exports = { createClockRouter };
