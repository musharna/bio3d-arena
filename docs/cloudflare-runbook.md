# Putting the arena behind Cloudflare

Two problems, one change.

**No analytics.** Measured 2026-08-01: the site had no analytics of any kind, so "are we getting
visitors" was unanswerable. The only evidence in the database is a `comparison` row — and `/arena`
auto-loads a ballot, so crawlers create those too. On 2026-07-29 that produced 199 ballot rows and
**zero** votes.

**Meshes are slow at the origin.** `media_asset` streams every GLB through the app: a full
server-side S3 read into memory, then a response, with no CDN. Measured over Fast 4G, one ballot
moved 8.37 MB in 35.4 s while a _larger_ 8.96 MB ballot took 19.6 s — the 4 Mbit/s floor for
8.37 MB is ~16.7 s, so that run served at roughly half the expected throughput. That variance is
origin latency, not bandwidth.

Cloudflare fixes both: free cookieless Web Analytics, and edge caching that the
`Cache-Control: public, max-age=3600` + `ETag` headers (PR #130) make effective immediately.

## Why not serve meshes straight from R2

Checked before planning this, and it is disqualified. **299 of 527 asset keys are descriptive**:

    commissioned/openrouter-anthropic-claude-opus-4-8_11.glb
    agentic/...
    gold/task11__bad.glb

`/media/o/{id}` exists precisely to hide those. Public R2 URLs would let any voter read the model
name out of devtools — and `gold/taskNN__bad.glb` would reveal which output is the deliberate bad
decoy, breaking both the blind comparison and the attention check. Serving direct from R2 requires
renaming all 299 objects to opaque hashes first. Cloudflare in front of the app route keeps the
indirection and still gets the edge cache.

## Before the DNS change — already done in code

`_client_ip` used to take `x-forwarded-for.split(",")[0]`, which is safe only if the proxy
REPLACES a client-supplied header. Cloudflare does not; its documentation says it "will append the
IP address of the HTTP proxy connecting to Cloudflare to the header". Behind Cloudflare, a request
carrying `X-Forwarded-For: 1.2.3.4` reaches the origin as `1.2.3.4, <real client>` — so element
[0] is attacker-chosen, and a vote farmer rotating it would never meet the per-IP cap.

Now resolved in preference order, each trusted only when its flag is set:
`CF-Connecting-IP` → `Fly-Client-IP` → `X-Forwarded-For` → socket peer.
Covered by `tests/test_client_ip_trust.py`.

## The two halves split, and only one needs a domain

**Analytics does NOT require DNS.** Cloudflare Web Analytics is "privacy-first analytics for your
website without changing DNS or using Cloudflare proxy" — a beacon snippet is enough. So "are we
getting visitors" can be answered today, on `taxon3d.org`, with nothing but a token.

**The CDN half DOES require a domain you own.** Putting a site behind Cloudflare's proxy means
adding it as a zone and pointing a registrar's nameservers at Cloudflare — impossible for a
`*.fly.dev` hostname, which belongs to Fly. Edge caching therefore waits on acquiring a domain,
and moving the public hostname carries an SEO cost: the sitemap, canonical links and OG tags all
currently advertise `taxon3d.org`, so the indexed URLs would need redirects and
re-verification.

## Steps A — analytics only (no domain, ~5 minutes)

1. Cloudflare dashboard → **Web Analytics** → _Add a site_ → hostname `taxon3d.org`.
2. Copy the beacon **token**.
3. `fly secrets set --app bio3d-arena BIO3D_CF_ANALYTICS_TOKEN=<token>`, then redeploy.
4. Do **NOT** set `BIO3D_BEHIND_CLOUDFLARE`. Traffic is not flowing through Cloudflare, so
   nothing is stripping `CF-Connecting-IP` and it must stay untrusted.

Verify: `curl -s https://taxon3d.org/ | grep -c cloudflareinsights` → `1`.

## Steps B — full proxy for edge caching (needs a domain you own)

1.  **Add the site** in Cloudflare and let it scan DNS.
2.  **Point the record at Fly.** For an apex/subdomain served by Fly, a proxied (orange-cloud)
    `CNAME` to `taxon3d.org`. The orange cloud is the part that matters — grey-cloud is
    DNS-only and gives neither caching nor analytics.
3.  **Update the nameservers** at the registrar to the pair Cloudflare shows. Propagation is
    usually minutes.
4.  **SSL/TLS mode: Full (strict).** Fly already terminates TLS with a valid certificate;
    "Flexible" would downgrade the origin hop to plaintext.
5.  **Turn on Web Analytics** for the hostname and copy the beacon token.
6.  **Set the app secrets:**

        fly secrets set --app bio3d-arena \
          BIO3D_BEHIND_CLOUDFLARE=true \
          BIO3D_CF_ANALYTICS_TOKEN=<token from step 5>

    `BIO3D_BEHIND_CLOUDFLARE` is what makes `CF-Connecting-IP` authoritative. **Set it only once
    traffic actually arrives via Cloudflare** — turning it on while requests still reach Fly
    directly would trust a header nothing is stripping.

7.  **If the hostname changes** (e.g. to a custom domain), also update `BIO3D_PUBLIC_BASE_URL` in
    `fly.toml`, or canonical links, OG tags and the sitemap keep advertising the old host.

## Verify after the flip

    # served through Cloudflare?
    curl -sI https://<host>/ | grep -iE 'server|cf-ray'          # expect server: cloudflare

    # meshes cached AT THE EDGE, not just cacheable
    curl -sI https://<host>/media/o/553.glb | grep -iE 'cf-cache-status|cache-control|age'
    # first hit MISS, second HIT

    # the beacon renders
    curl -s https://<host>/ | grep -c cloudflareinsights          # expect 1

    # rate limiting still sees distinct clients: send a bogus XFF and confirm it is IGNORED
    curl -sI -H 'X-Forwarded-For: 1.2.3.4' https://<host>/healthz

A `cf-cache-status: HIT` on the second mesh request is the whole point — that is the origin round
trip disappearing.

## What this does NOT fix

Ballots are still ~8 MB. Cloudflare removes the origin round trip and serves repeat requests from
the edge; it does not make the corpus lighter. Decimation/LOD remains the open lever, and the
measured 20.9 s time-to-comparable is dominated by transferring those bytes at all.
