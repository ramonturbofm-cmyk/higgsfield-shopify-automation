/**
 * Higgsfield <-> Shopify automation server
 * ------------------------------------------------------------
 * Flow:
 *   1. Shopify fires a "products/create" webhook whenever a new product
 *      is added to your store.
 *   2. This server verifies the webhook, then asks Higgsfield to generate
 *      a product IMAGE (text-to-image) based on the product title/description.
 *   3. When Higgsfield finishes, it calls our /webhooks/higgsfield endpoint.
 *      We attach the finished image to the Shopify product, then kick off
 *      a VIDEO generation (image-to-video) using that new image as input.
 *   4. When the video finishes, Higgsfield calls us again and we attach
 *      the video to the same product.
 *
 * IMPORTANT — please read before deploying:
 * - This server must run somewhere with a public HTTPS URL that is online
 *   24/7 (e.g. Render, Railway, Fly.io, a small VPS). It can NOT run inside
 *   a Claude session — those are ephemeral and are not reachable from the
 *   internet, so they can't receive Shopify/Higgsfield webhooks.
 * - Higgsfield's public docs don't publish an exact webhook payload schema
 *   or signing scheme at the time this was written. The parsing in
 *   `extractHiggsfieldResult()` below and the shared-secret check in
 *   `verifyHiggsfieldWebhook()` are best-effort based on their documented
 *   /v1/generations endpoint — check the "Webhooks" section of your own
 *   Higgsfield dashboard/docs and adjust those two functions if the real
 *   payload looks different. Everything else (Shopify side) is based on
 *   Shopify's current Admin GraphQL API and should work as-is.
 * - Uses a local JSON file (jobs.json, via lowdb) purely as a simple job
 *   log/lookup so retries and debugging are easier. Fine for one store;
 *   swap for a real database if you scale this up.
 */

require('dotenv').config();
const crypto = require('crypto');
const express = require('express');
const fetch = require('node-fetch');
const low = require('lowdb');
const FileSync = require('lowdb/adapters/FileSync');

const {
  SHOPIFY_STORE_DOMAIN,
  SHOPIFY_ADMIN_ACCESS_TOKEN,
  SHOPIFY_API_VERSION = '2024-10',
  SHOPIFY_WEBHOOK_SECRET,
  HIGGSFIELD_API_KEY,
  HIGGSFIELD_API_BASE = 'https://cloud.higgsfield.ai',
  HIGGSFIELD_IMAGE_MODEL = 'flux',
  HIGGSFIELD_VIDEO_MODEL = 'default-video-model',
  HIGGSFIELD_WEBHOOK_SHARED_SECRET,
  PUBLIC_BASE_URL,
  BRAND_STYLE_PROMPT = '',
  PORT = 3000,
} = process.env;

for (const [name, val] of Object.entries({
  SHOPIFY_STORE_DOMAIN, SHOPIFY_ADMIN_ACCESS_TOKEN, SHOPIFY_WEBHOOK_SECRET,
  HIGGSFIELD_API_KEY, HIGGSFIELD_WEBHOOK_SHARED_SECRET, PUBLIC_BASE_URL,
})) {
  if (!val) console.warn(`[config] Warning: ${name} is not set — server will not work correctly until it is.`);
}

const adapter = new FileSync('jobs.json');
const db = low(adapter);
db.defaults({ jobs: [] }).write();

const app = express();

// --- Shopify webhook needs the RAW body to verify the HMAC signature, so
// we capture it before express.json() parses anything. ---
app.use('/webhooks/shopify', express.raw({ type: 'application/json' }));
app.use(express.json());

// ---------------------------------------------------------------------
// Helpers: Shopify
// ---------------------------------------------------------------------

function verifyShopifyWebhook(req) {
  const hmacHeader = req.get('X-Shopify-Hmac-Sha256');
  if (!hmacHeader || !SHOPIFY_WEBHOOK_SECRET) return false;
  const digest = crypto
    .createHmac('sha256', SHOPIFY_WEBHOOK_SECRET)
    .update(req.body) // raw Buffer
    .digest('base64');
  try {
    return crypto.timingSafeEqual(Buffer.from(digest), Buffer.from(hmacHeader));
  } catch {
    return false; // length mismatch etc.
  }
}

async function shopifyGraphQL(query, variables) {
  const res = await fetch(
    `https://${SHOPIFY_STORE_DOMAIN}/admin/api/${SHOPIFY_API_VERSION}/graphql.json`,
    {
      method: 'POST',
      headers: {
        'Content-Type': 'application/json',
        'X-Shopify-Access-Token': SHOPIFY_ADMIN_ACCESS_TOKEN,
      },
      body: JSON.stringify({ query, variables }),
    }
  );
  const json = await res.json();
  if (json.errors) {
    throw new Error(`Shopify GraphQL error: ${JSON.stringify(json.errors)}`);
  }
  return json.data;
}

const CREATE_MEDIA_MUTATION = /* GraphQL */ `
  mutation productCreateMedia($productId: ID!, $media: [CreateMediaInput!]!) {
    productCreateMedia(productId: $productId, media: $media) {
      media {
        alt
        mediaContentType
        status
      }
      mediaUserErrors {
        field
        message
      }
    }
  }
`;

/**
 * Attaches an already-hosted image or video (by URL) to a Shopify product.
 * NOTE: Shopify fetches the file from `originalSource` itself, so that URL
 * must be public and reachable by Shopify's servers (no auth headers).
 * For video files over ~1GB or if Shopify can't fetch the URL directly,
 * switch to the `stagedUploadsCreate` + upload flow instead.
 */
async function attachMediaToProduct(productGid, sourceUrl, contentType, alt) {
  const data = await shopifyGraphQL(CREATE_MEDIA_MUTATION, {
    productId: productGid,
    media: [{ originalSource: sourceUrl, mediaContentType: contentType, alt }],
  });
  const errors = data.productCreateMedia.mediaUserErrors;
  if (errors && errors.length) {
    throw new Error(`Shopify media error: ${JSON.stringify(errors)}`);
  }
  return data.productCreateMedia.media;
}

// ---------------------------------------------------------------------
// Helpers: Higgsfield
// ---------------------------------------------------------------------

function verifyHiggsfieldWebhook(req) {
  // Best-effort check via a shared secret we control (see .env.example).
  // Replace with real signature verification if/when Higgsfield documents one.
  return req.query.secret === HIGGSFIELD_WEBHOOK_SHARED_SECRET;
}

async function higgsfieldRequest(path, body) {
  const res = await fetch(`${HIGGSFIELD_API_BASE}${path}`, {
    method: 'POST',
    headers: {
      'Content-Type': 'application/json',
      Authorization: `Bearer ${HIGGSFIELD_API_KEY}`,
    },
    body: JSON.stringify(body),
  });
  if (!res.ok) {
    throw new Error(`Higgsfield API error ${res.status}: ${await res.text()}`);
  }
  return res.json();
}

function buildWebhookUrl(type, productGid, jobId) {
  const url = new URL('/webhooks/higgsfield', PUBLIC_BASE_URL);
  url.searchParams.set('type', type);
  url.searchParams.set('productId', productGid);
  url.searchParams.set('jobId', jobId);
  url.searchParams.set('secret', HIGGSFIELD_WEBHOOK_SHARED_SECRET);
  return url.toString();
}

function requestImageGeneration({ productGid, title, description }) {
  const jobId = crypto.randomUUID();
  const prompt = [title, description, BRAND_STYLE_PROMPT].filter(Boolean).join('. ');
  return higgsfieldRequest('/v1/generations', {
    task: 'text-to-image',
    model: HIGGSFIELD_IMAGE_MODEL,
    prompt,
    width: 1536,
    height: 1536,
    webhook_url: buildWebhookUrl('image', productGid, jobId),
  }).then((res) => ({ jobId, generationId: res.id, prompt }));
}

function requestVideoGeneration({ productGid, imageUrl, title }) {
  const jobId = crypto.randomUUID();
  const prompt = [title, BRAND_STYLE_PROMPT].filter(Boolean).join('. ');
  return higgsfieldRequest('/v1/generations', {
    task: 'image-to-video',
    model: HIGGSFIELD_VIDEO_MODEL,
    input_image: imageUrl,
    duration: 6,
    fps: 24,
    motion_intensity: 'medium',
    prompt,
    webhook_url: buildWebhookUrl('video', productGid, jobId),
  }).then((res) => ({ jobId, generationId: res.id, prompt }));
}

/**
 * Higgsfield's exact completion payload isn't publicly documented in detail.
 * This tries a few plausible shapes so you only have to fix ONE place once
 * you've seen a real payload (log it — see the /webhooks/higgsfield handler).
 */
function extractHiggsfieldResult(body) {
  const assetUrl =
    body.result?.url ||
    body.output?.url ||
    body.asset_url ||
    body.url ||
    (Array.isArray(body.outputs) && body.outputs[0]?.url) ||
    null;
  const status = body.status || body.state || 'unknown';
  return { assetUrl, status };
}

// ---------------------------------------------------------------------
// Route: Shopify -> new product created -> kick off image generation
// ---------------------------------------------------------------------

app.post('/webhooks/shopify/products-create', async (req, res) => {
  if (!verifyShopifyWebhook(req)) {
    return res.status(401).send('Invalid HMAC');
  }

  const product = JSON.parse(req.body.toString('utf8'));
  const productGid = `gid://shopify/Product/${product.id}`;
  const title = product.title || '';
  const description = (product.body_html || '').replace(/<[^>]+>/g, ' ').trim();

  // Respond fast; do the (slow) generation kickoff after responding so
  // Shopify doesn't retry the webhook on timeout.
  res.status(200).send('ok');

  try {
    const { jobId, generationId, prompt } = await requestImageGeneration({
      productGid,
      title,
      description,
    });
    db.get('jobs')
      .push({
        jobId,
        type: 'image',
        productGid,
        title,
        generationId,
        prompt,
        status: 'requested',
        createdAt: new Date().toISOString(),
      })
      .write();
    console.log(`[image] requested for ${title} (${productGid}), job ${jobId}`);
  } catch (err) {
    console.error(`[image] failed to request generation for ${productGid}:`, err.message);
  }
});

// ---------------------------------------------------------------------
// Route: Higgsfield -> generation finished
// ---------------------------------------------------------------------

app.post('/webhooks/higgsfield', async (req, res) => {
  if (!verifyHiggsfieldWebhook(req)) {
    return res.status(401).send('Invalid secret');
  }
  res.status(200).send('ok');

  const { type, productId: productGid, jobId } = req.query;
  console.log(`[higgsfield webhook] type=${type} product=${productGid} job=${jobId}`, JSON.stringify(req.body));

  const { assetUrl, status } = extractHiggsfieldResult(req.body);
  const job = db.get('jobs').find({ jobId }).value();

  if (status !== 'completed' && status !== 'succeeded' && !assetUrl) {
    // Not done yet, or failed — log and bail. Adjust the status strings
    // above once you've confirmed Higgsfield's real status values.
    if (job) db.get('jobs').find({ jobId }).assign({ status, updatedAt: new Date().toISOString() }).write();
    return;
  }
  if (!assetUrl) {
    console.error(`[higgsfield webhook] no asset URL found in payload for job ${jobId}`);
    return;
  }

  try {
    if (type === 'image') {
      await attachMediaToProduct(productGid, assetUrl, 'IMAGE', job?.title || 'AI-generated product image');
      console.log(`[image] attached to ${productGid}`);
      if (job) db.get('jobs').find({ jobId }).assign({ status: 'attached', assetUrl }).write();

      // Chain: now generate a video using this freshly generated image.
      const video = await requestVideoGeneration({
        productGid,
        imageUrl: assetUrl,
        title: job?.title,
      });
      db.get('jobs')
        .push({
          jobId: video.jobId,
          type: 'video',
          productGid,
          title: job?.title,
          generationId: video.generationId,
          prompt: video.prompt,
          status: 'requested',
          createdAt: new Date().toISOString(),
        })
        .write();
      console.log(`[video] requested for ${productGid}, job ${video.jobId}`);
    } else if (type === 'video') {
      await attachMediaToProduct(productGid, assetUrl, 'VIDEO', job?.title || 'AI-generated product video');
      console.log(`[video] attached to ${productGid}`);
      if (job) db.get('jobs').find({ jobId }).assign({ status: 'attached', assetUrl }).write();
    }
  } catch (err) {
    console.error(`[higgsfield webhook] failed to process job ${jobId}:`, err.message);
    if (job) db.get('jobs').find({ jobId }).assign({ status: 'error', error: err.message }).write();
  }
});

// ---------------------------------------------------------------------

app.get('/health', (_req, res) => res.send('ok'));
app.get('/jobs', (_req, res) => res.json(db.get('jobs').value()));

app.listen(PORT, () => {
  console.log(`Higgsfield <-> Shopify automation server listening on port ${PORT}`);
});
