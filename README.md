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
worker/            optional feed proxy: live news + live market prices
versions/          frozen snapshots of earlier versions
```

Upload the five files at the top level. `versions/` is history for you -
it doesn't need to go on the server (though it's harmless if it does).

Keep them together - both pages link to `style.css` and `script.js` by relative path, and to each other.
The only inline script is one line in `<head>` that marks JS as running; it has
to stay inline because an external file would load too late to prevent a flash
of hidden content.

## Before you upload - 2 things to set

Open `script.js`, find the CONFIG block at the top:

```js
const CONTACT_EMAIL   = "hello@resolv.trade";   // where access requests go
const WAITLIST_ENDPOINT = "";                    // optional POST url
```

- **`CONTACT_EMAIL`** - your real address. Used by the footer Contact link and
  as the fallback for the access form.
- **`WAITLIST_ENDPOINT`** - **set this before launch.** Paste a form endpoint
  (Formspree, Buttondown, ConvertKit, your own API) and the form POSTs
  `{email, source}` as JSON and confirms inline. While it's empty, addresses
  are only saved in each visitor's own browser (localStorage) - they never
  reach you. The form **never says "recorded" unless the save or POST actually
  succeeded** - on failure it shows your email as a fallback.
  When you set it, also add the endpoint's origin to `connect-src` in the CSP,
  in three places: `index.html`, `methodology.html`, and `_headers`.

Then update the domain in `index.html` (search for `resolv.trade`):
`<link rel="canonical">` and `og:url`.

## Making the live data actually work

Out of the box the page fetches news straight from the visitor's browser. That
works, but it has limits you cannot fix in frontend code:

- GDELT allows about **one request per 5 seconds per IP**, so a visitor on a
  shared IP (office, campus, mobile carrier, VPN) sees the standby state.
- GDELT's rate-limit response carries no CORS headers, so the browser reports
  a generic failure and the page cannot tell a rate limit from an outage.
- Kalshi and Polymarket cannot be reached from the page at all, which is why
  the market tape is labelled SAMPLE.

**`worker/` fixes all three.** It is a small Cloudflare Worker (free tier) that
fetches news and both exchanges server-side, caches for 60 seconds, and serves
one JSON payload to the site. Deploy it, set `FEED_PROXY` in `script.js`, and
the tape and impact panel relabel themselves from amber SAMPLE to green LIVE
automatically, because the labels follow the data. Full instructions in
[`worker/README.md`](worker/README.md).

Leaving `FEED_PROXY` empty keeps today's behaviour, which is a perfectly valid
place to launch from.

## Security (already wired in - keep it when editing)

The site is static with no accounts, no cookies and no secrets, so the attack
surface is small; what exists is locked down:

- **Content Security Policy** - a `<meta>` CSP in both HTML files plus the
  `_headers` file for the host. Only same-origin scripts (plus the one hashed
  inline line), Google Fonts styles/fonts, the GDELT API for `fetch`, and
  `data:` images are allowed. Everything else - frames, objects, other
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
  before display, and stores at most 200 addresses locally.
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
- **It cannot run the feed Worker** in `worker/`. That is fine: the Worker
  deploys separately to Cloudflare and the site simply calls its URL.

You do not need to upload `versions/`, `worker/` or `README.md`. They are
harmless if you do, but `versions/` publishes every old design of the site at
a guessable URL, so you may prefer to leave it out.

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

Hero (with a verdict card showing the whole product in one glance) → four quick
numbers → what it does in three cards → **the gap** scroll scene → **proof**
(calibration chart + three honest numbers) → the live news feed → pricing with
the free trial → signup.

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
| **Calibration chart** | both | Static canvas. Figures are in the `BINS` array in `script.js`. |
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
- The hero card is badged **EXAMPLE** and the calibration figures **VALIDATION
  RUN**. They're honest labels - keep them until the numbers are live, then
  swap the label rather than removing it.
- Respects `prefers-reduced-motion`, works without JavaScript (content is only
  hidden for animation when JS is running), and has no horizontal overflow at
  390 / 768 / 1280 / 1440 px.
- The terminal and evidence-graph apps live in `../Prototype/` and are **not**
  linked from either page. If you later want a public demo, upload one and point
  the CTAs at it.
- Pricing is a 14-day free trial with no card, then $20 Basic / $99 Pro / custom
  Desk. The same figures appear in `../Prototype/Resolv Terminal - Functional.html`
  - keep them in sync if you change them.
