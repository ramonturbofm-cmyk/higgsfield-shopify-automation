# Higgsfield ↔ Shopify automation

Automatically generates an AI product image and product video with Higgsfield
every time a new product is created in your Shopify store, and attaches both
to that product.

```
Shopify (new product)
   │  webhook: products/create
   ▼
this server ──► Higgsfield: generate image
                        │  webhook when done
                        ▼
              this server ──► attach image to Shopify product
                        │
                        └──► Higgsfield: generate video from that image
                                     │  webhook when done
                                     ▼
                        this server ──► attach video to Shopify product
```

## 1. Requirements

- Node.js 18+
- A place to host this server 24/7 with a public HTTPS URL — this can **not**
  run inside a Claude/Cowork session, those don't stay online or accept
  inbound webhooks. Cheap options: [Render](https://render.com),
  [Railway](https://railway.app), [Fly.io](https://fly.io), or any VPS.
- A Shopify custom app with an Admin API access token (`write_products`,
  `read_products` scopes).
- A Higgsfield account + API key (cloud.higgsfield.ai).

## 2. Install

```bash
npm install
cp .env.example .env
# fill in .env with your real values
```

## 3. Deploy first, configure webhooks second

Webhooks need a real public URL to point at, so:

1. Deploy this server (e.g. push to Render/Railway) and note its public URL,
   e.g. `https://turbo-records-automation.onrender.com`.
2. Put that URL in `.env` as `PUBLIC_BASE_URL`.
3. Redeploy with the finished `.env` values in place.

## 4. Create the Shopify custom app + webhook

1. Shopify Admin → **Settings → Apps and sales channels → Develop apps →
   Create an app**. Give it `read_products` + `write_products` scopes and
   install it. Copy the **Admin API access token** into `.env`.
2. Still in Shopify Admin, create a webhook for the **`products/create`**
   topic pointing at:
   `https://<your-server>/webhooks/shopify/products-create`
   (Format: JSON). Shopify will show you a signing secret — put it in
   `.env` as `SHOPIFY_WEBHOOK_SECRET`.
   - You can do this from Settings → Notifications → Webhooks in the
     Shopify Admin UI, or via the Admin API. I can also register it for you
     through the Shopify connection I already have — just say the word once
     your server is deployed and give me the URL, since creating a webhook
     is a standing integration change I'll only do with your go-ahead.

## 5. Higgsfield setup

1. Get your API key from the Higgsfield dashboard → `.env` as
   `HIGGSFIELD_API_KEY`.
2. **Check Higgsfield's current webhook docs in your own dashboard** —
   public documentation on the exact webhook payload shape and signing
   method is thin at the time this was built. Two spots in `server.js` are
   marked for you to double check once you've triggered one real
   generation and can see the actual payload Higgsfield sends:
   - `extractHiggsfieldResult()` — pulls the finished asset's URL out of
     the webhook body. It tries several common field names; adjust to match
     what you actually see logged.
   - `verifyHiggsfieldWebhook()` — currently checks a shared secret *we*
     put in the callback URL (simple and secure enough), since Higgsfield's
     own signing scheme wasn't documented publicly. Swap in real signature
     verification if they support one.
   - Tip: watch the server logs (or `GET /jobs`) after your first test
     product — every incoming Higgsfield webhook body gets logged so you
     can see its real shape immediately.

## 6. Test it

1. Create a test product in Shopify.
2. Watch your server logs — you should see `[image] requested for ...`.
3. When Higgsfield finishes the image, logs show `[image] attached` and
   `[video] requested`.
4. When the video finishes, logs show `[video] attached`.
5. Check the product in Shopify Admin — the generated image and video
   should now be in its media gallery.
6. `GET https://<your-server>/jobs` shows the full job history for
   debugging.

## Notes & things you may want to tune

- **Prompt**: built from the product title + description (HTML stripped) +
  `BRAND_STYLE_PROMPT` from `.env`. Edit `requestImageGeneration` /
  `requestVideoGeneration` in `server.js` to change this — e.g. pull in
  product tags, product type, or a per-collection style.
- **Video source image**: currently chained off the AI-generated image
  (not the product's real photo, if it has one already). If you'd rather
  animate an existing product photo when one was uploaded manually, that's
  a small change in the `products/create` handler.
- **Large videos**: `productCreateMedia` expects a publicly fetchable URL;
  Shopify pulls the file itself. If a generated video ever fails to attach
  because it's too large or Shopify can't fetch it, switch to Shopify's
  `stagedUploadsCreate` mutation + direct upload flow instead.
- **Job storage**: uses a local `jobs.json` file (via lowdb) for simplicity.
  Fine for a single store; move to a real database if this becomes
  business-critical or you scale to many stores.
- **Retries**: Shopify retries a webhook if it doesn't get a 200 quickly —
  this server responds immediately and does the slow work after, so that's
  already handled. Higgsfield's own retry behavior on failed callbacks
  isn't documented; keep an eye on `/jobs` for stuck entries early on.
