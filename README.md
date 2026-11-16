> **October 8 update:** See [the connected workspace guide](../WORKSPACE.md) for the new shared local server, live adapters and corrected training workflow. Older setup and model-state notes below describe the original prototype.

# Resolv - public website

The complete marketing site. No build step, no dependencies, no terminal app
inside it. Upload the folder and it works.

```
index.html         the main page          ← always the newest version
methodology.html   the deep-dive page
404.html           branded not-found page
style.css          all styling (both pages)
script.js          all behaviour (both pages)
CNAME              custom domain, required by GitHub Pages
.nojekyll          stops GitHub running Jekyll over the site
robots.txt         search crawlers
sitemap.xml        search crawlers
_headers           security headers (Netlify/Cloudflare only, ignored by GitHub)
worker/            older optional feed proxy, superseded by the Resolv service (API_ORIGIN)
versions/          frozen snapshots of earlier versions
```

Upload the files at the top level (the three pages, `style.css`,
`script.js`, `CNAME`, `.nojekyll`, `robots.txt`, `sitemap.xml`, `_headers`).
`versions/`, `worker/` and this README stay off the server.

Keep them together - both pages link to `style.css` and `script.js` by relative path, and to each other.
The only inline script is one line in `<head>` that marks JS as running; it has
to stay inline because an external file would load too late to prevent a flash
of hidden content.

## Before you upload: the CONFIG block

Open `script.js` and read the CONFIG block at the top:

```js
const API_ORIGIN    = "";   // hosted Resolv service, e.g. "https://api.resolvterminal.com"
const CONTACT_EMAIL = "";   // a WORKING inbox; empty means no address is shown anywhere
```

Everything else is derived from those two values and from the page itself:

| Constant | Where it comes from | Effect |
|---|---|---|
| `WORKSPACE_MODE` | `<meta name="resolv-workspace" content="...">`, injected only by a Resolv server: `connected` (local workspace) or `hosted` (hosted service). Missing on the static site. | Workspace pages call their own origin. |
| `API_BASE` | `""` in either workspace mode, else `API_ORIGIN` | Prefix for every API call. |
| `WAITLIST_ENDPOINT` | `API_BASE + "/api/waitlist"` when a workspace or `API_ORIGIN` exists, else empty | Where the signup form POSTs `{email, source}`. |
| `FEED_PROXY` | `API_BASE + "/api/feed"`, same rule | News plus market quotes with per-source health. |
| `MODEL_STATUS_URL` | `API_BASE + "/api/model"`, same rule | The live status line under Proof and Calibration. |

- **`API_ORIGIN`**: leave it empty until the hosted service is actually
  running and `api.resolvterminal.com` resolves. When you set it, add the
  origin to `connect-src` in **four** places, all identical: `index.html`,
  `methodology.html`, `404.html` and `_headers`. If the service later fails,
  the public page falls back to fetching GDELT news directly, so the wire
  never goes dark because of it.
- **`CONTACT_EMAIL`**: empty on purpose. The old `hello@resolv.trade` is on a
  domain that does not exist (NXDOMAIN), and `resolvterminal.com` has no MX
  record, so no address is displayed anywhere. Set up a working inbox first
  (for example forwarding for an address on `resolvterminal.com`), then set it
  here and in `USER_AGENT` in `MLModel/resolv_ml/config.py`. It is only ever
  shown as a fallback when a signup POST fails.

### What the signup form does in each mode

The form **never says "recorded" unless the POST actually succeeded**, and it
never claims anything reached Resolv when it didn't.

| Mode | Button | After a valid email |
|---|---|---|
| Static site, no `API_ORIGIN` (today's public site) | Note my interest | Saved in the visitor's own browser only (localStorage key `resolv-waitlist`, at most 200 entries). The page says nothing was sent and that signups open at launch. You never see these addresses. |
| Connected local workspace | Request access | POST to the local server, saved in `signups.sqlite` in the ML home. Says email delivery is not configured. |
| Hosted, or static with `API_ORIGIN` set | Request access | POST to the hosted service. Says "Access request received." Export with the admin endpoint described in the deploy README (`deploy/README.md`). |

The pre-launch wording is in the HTML itself, so a visitor without
JavaScript sees the honest version too. There is no mailto fallback for the
pre-launch form.

## Making the live data actually work

Out of the box the page fetches news straight from the visitor's browser. That
works, but it has limits you cannot fix in frontend code:

- GDELT allows about **one request per 5 seconds per IP**, so a visitor on a
  shared IP (office, campus, mobile carrier, VPN) sees the standby state.
- GDELT's rate-limit response carries no CORS headers, so the browser reports
  a generic failure and the page cannot tell a rate limit from an outage.
- Kalshi and Polymarket cannot be reached from the page at all, which is why
  the market tape is labelled SAMPLE.

**The Resolv service fixes all three.** `/api/feed` fetches news and both
exchanges server-side, caches each source for 60 seconds, and returns one JSON
payload with per-source health. Set `API_ORIGIN` (or open the page from the
local workspace) and the tape and impact panel relabel themselves from amber
SAMPLE to green LIVE automatically, because the labels follow the data.

How the tape labels quotes (`marketFreshness` in `script.js`):

- It reads `d.sources.poly` and `d.sources.kalshi` (`status`, `fetchedAt`,
  `ageSeconds`). It never uses the browser clock as a quote time.
- **LIVE · REAL PRICES · 14:05** means every venue on the tape is `live`, and
  14:05 is the oldest server fetch time among them.
- **CACHED · REAL PRICES · 15 MIN AGO** means the server is serving a
  retained copy; the age is the server's own count.
- A venue that is stale or unavailable is named (e.g. `KALSHI UNAVAILABLE`).
- If the feed itself stops answering, quotes already shown flip to CACHED
  with their original fetch time and `FEED UNREACHABLE`.

The older Cloudflare Worker in `worker/` predates the service. Its payload has
no per-venue health, so with it the tape would say `AGE UNKNOWN`. Prefer the
service.

Leaving `API_ORIGIN` empty keeps today's behaviour (GDELT news straight from
the browser, SAMPLE tape), which is a perfectly valid place to launch from.

## Security (already wired in - keep it when editing)

The site is static with no accounts, no cookies and no secrets, so the attack
surface is small; what exists is locked down:

- **Content Security Policy** - a `<meta>` CSP in all three HTML files plus
  the `_headers` file for the host. Only same-origin scripts (plus the one
  hashed inline line), Google Fonts styles/fonts, the GDELT API for `fetch`,
  and `data:` images are allowed. `connect-src` is identical in all four
  places; keep it that way. The `script-src` hash covers the one-line inline
  `<script>` in `<head>`; if that line changes, recompute the base64 sha256
  of the exact bytes between the tags and update all four. Keep
  `upgrade-insecure-requests` in these static files; the local workspace
  server strips it when serving over plain http. Everything else - frames, objects, other
  origins - is blocked. If you add any external resource, add its origin to
  the CSP or the browser will refuse to load it.
- **`_headers`** - Netlify/Cloudflare Pages pick it up automatically; other
  hosts need the same headers in their config (nginx `add_header`, Apache
  `Header set`, or `vercel.json`). It adds `frame-ancestors` (clickjacking),
  `nosniff`, HSTS, a strict referrer policy and a locked-down
  `Permissions-Policy`.
- **External data is treated as hostile.** Every string from the news feed is
  HTML-escaped (`esc()`), length-capped, and URLs only become links if they
  are plain `http(s)` (`safeUrl()` drops `javascript:` and friends). External
  links carry `rel="noopener noreferrer"`.
- **The email form** validates and caps input (maxlength 254), escapes it
  before display, and, pre-launch, stores at most 200 addresses in the
  visitor's own browser.
- Nothing uses `eval`, inline event handlers, `document.write`, or third-party
  scripts. Keep it that way: new dynamic HTML must go through `esc()`, new
  URLs through `safeUrl()`.

## Deploying

Any static host works - upload the files together, including `_headers`
(or mirror its headers in your server config; see the Security section).

| Host | How |
|---|---|
| **Netlify / Vercel** | Drag the `Website` folder onto the dashboard drop zone |
| **GitHub Pages** | Push the folder, enable Pages on the branch. See the section below, there are 4 GitHub-specific files |
| **Cloudflare Pages** | Connect the repo, no build command, output dir `/` |
| **Your own server** | `scp index.html methodology.html style.css script.js user@host:/var/www/html/` |

Point your domain at the host and you're live. Nothing needs Node, npm, or a
build pipeline.

### GitHub Pages specifics (this is where resolvterminal.com runs)

Four files exist purely for GitHub. Upload all of them along with the pages:

- **`CNAME`** contains `resolvterminal.com`. GitHub reads this to keep the
  custom domain attached. If it is missing, the domain lives only in the repo
  Settings and can be dropped when you redeploy or switch branches, which
  takes the site offline until you re-enter it by hand.
- **`.nojekyll`** turns off Jekyll. By default GitHub runs every Pages site
  through Jekyll, which **silently ignores any file or folder starting with an
  underscore or a dot**. That is a trap waiting to happen. It also makes
  builds faster.
- **`robots.txt`** and **`sitemap.xml`** tell search engines the site exists
  and which two pages matter. Update `sitemap.xml` if you add pages.

Also worth doing once, in the repo under **Settings, then Pages**:

- Tick **Enforce HTTPS** once the certificate has been issued.
- Confirm the custom domain field reads `resolvterminal.com`.

Two things GitHub Pages cannot do, both harmless if you know about them:

- **`_headers` does nothing there.** That format belongs to Netlify and
  Cloudflare Pages. GitHub Pages cannot send custom headers at all, so the
  clickjacking, HSTS and permissions headers are inactive. The CSP still
  applies because it is a `<meta>` tag inside each page. Moving to Cloudflare
  Pages later activates the whole set with no code changes.
- **It cannot run the Resolv service.** That is fine: the service runs
  separately (see the deploy README, `deploy/README.md`) and the site simply calls
  `API_ORIGIN`.

Do not upload `versions/`, `worker/` or `README.md`. This README is internal
notes, and `versions/` publishes every old design of the site at a guessable
URL.

## Optional: a social preview image

The Open Graph tags are set except for the image. If you want a rich preview
card when the link is shared, add a 1200×630 PNG next to `index.html` and add:

```html
<meta property="og:image" content="https://yourdomain.com/og.png">
<meta name="twitter:image" content="https://yourdomain.com/og.png">
```

## How the two pages split

**`index.html` answers four questions and stops.** What is this, what does it
do, does it actually work, what does it cost. ~650 words:

Hero (with an EXAMPLE verdict card showing the whole product in one glance) →
four quick facts → what it does in three cards → **the gap** scroll scene
(ILLUSTRATIVE) → **proof** (how a model is judged against the market, the gate
it must clear, and the dated status: no model has beaten the market on
held-out data yet) → the live news feed → pricing with the free trial →
signup.

**`methodology.html` is where the depth lives.** The five-stage pipeline, the
two model families with their formulas, the evidence-graph scene, resolution
risk, how calibration is scored, and the FAQ. Linked from the nav, the footer,
and inline wherever a claim on the main page needs backing.

Keeping the maths off the main page is deliberate - a visitor who wants it will
click, and one who doesn't shouldn't have to scroll past it.

### The moving parts

| Piece | Where | Notes |
|---|---|---|
| **The gap** scene | index | Scroll-scrubbed: the price lags fair value after news, the shaded window grows then closes. |
| **Evidence graph** scene | methodology | Assembles edge by edge as you scroll. |
| **Calibration chart** | both | ILLUSTRATIVE canvas: the diagonal plus a textbook overconfident curve. No data, no dots, no sample sizes. Replace it with real points only from a scored held-out report. |
| **Model status line** | both | `[data-model-status]`, filled from `/api/model` only when a Resolv server is reachable. A failed call says unavailable, never 0. |
| **Workspace banner** | both | Connected workspace only: links into all ten terminal views (`/terminal#app/<view>`), plus `[data-workspace-view]` links next to matching sections. |
| **Stage rail** | methodology | Five stages in the `STAGES` array in `script.js` - detail copy, a GUARANTEE line, and a mini-panel each. Auto-advances until clicked. |
| **Live feed** | index | Real public headlines, 90s auto-refresh, ticking timestamps, honest failure state. |

Scenes are tall `.scene` blocks with a `position:sticky` inner panel. Progress
comes from geometry in one passive scroll listener - no library, no
`requestAnimationFrame` dependency, and the right state is known on load. To
change a scene's pace edit `.scene{height:300vh}`; taller is slower.

`script.js` is shared by both pages and every block is guarded, so each page
only runs the parts it actually has.

## Notes

- The live news panel fetches a public feed directly from the visitor's browser.
  If it's blocked or rate-limited, the panel says so and offers a retry - it
  never invents headlines. That honesty is deliberate and worth keeping.
- Honesty rules for every number on both pages: worked figures are badged
  **EXAMPLE**, drawn concepts **ILLUSTRATIVE**, and nothing claims to be a
  result unless it can be traced to a report. The Proof and Calibration
  sections state, with a date (October 9, 2026), that no model has beaten the
  market on held-out data. The gate figures there mirror
  `MLModel/resolv_ml/config.py` (`PROMOTION_MARGIN`, `GATE_MIN_EVENTS`,
  `GATE_MIN_RESOLUTION_DAYS`, `BOOTSTRAP_*`, `DEMOTE_MIN_SCORED`); keep them in
  sync. When a model is promoted, update the dated lines from its report.
- The old "73% of our confident calls" counter, the hard-coded calibration
  bins and the Brier/log-loss table were removed on October 9, 2026: none of
  them could be traced to any report.
- Respects `prefers-reduced-motion`, works without JavaScript (content is only
  hidden for animation when JS is running), and has no horizontal overflow at
  390 / 768 / 1280 / 1440 px.
- The terminal lives in `../Prototype/`. It is linked **only** when the page
  is served by the connected local workspace (`WORKSPACE_MODE === "connected"`):
  a banner with all ten views (Briefing, Markets, News wire, Evidence, Model,
  Simulation, Sizing, Cross-venue, Watchlist, Portfolio) via
  `/terminal#app/<view>`. The public and hosted pages never link a terminal.
- Pricing is a 14-day free trial with no card, then $12 Basic / $39 Pro / custom
  Desk. The same figures appear in `../Prototype/Resolv Terminal.html`
  - keep them in sync if you change them.
