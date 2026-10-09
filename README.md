# Resolv

Public market research terminal and website, with a local pricing and ML lab.

## Public website

The root HTML/CSS/JavaScript files remain the GitHub Pages website at
https://resolvterminal.com. `terminal.html` opens all ten research views.

The Pages workflow fetches public Kalshi quotes, Polymarket quotes and BBC
Business RSS, then publishes only the website assets and `data/feed.json`.
Snapshots are scheduled approximately every ten minutes; GitHub Actions can
delay schedules. The terminal marks data older than 15 minutes stale and
retains per-source timestamps and error states. This is polled data, not an
exchange streaming connection. Failed sources never invent replacement rows.
The build keeps a best-effort cache of last successful source data.

To activate after reviewing and merging the PR:

1. In **Settings → Pages → Build and deployment**, select **GitHub Actions**.
2. Keep the existing custom domain `resolvterminal.com` and HTTPS setting.
3. Run **Actions → Publish website and market snapshots → Run workflow**.
4. Confirm the deployment succeeds and source ages are visible in the terminal.

No exchange API keys are required. GitHub Actions needs to be enabled and the
`github-pages` environment must permit deployments from `main`. Public-repo
scheduled workflows can stop after prolonged repository inactivity; check the
Actions page when source data remains stale. News reuse remains subject to the
publisher's feed terms.

## Local workspace and model

Double-click **Start Resolv.command** to serve the website, terminal, feeds,
pricing and ML status together on http://127.0.0.1:8899. First-time model setup:

```sh
cd MLModel
./setup.sh
```

**Train Resolv.command** collects public histories, backfills archived events
and fits candidates. Data and artifacts stay under `runtime/` and are ignored
by Git. Nothing here connects to an exchange account or places orders.

The public terminal supports search, filters, current quotes, evidence context,
hypothetical sizing/simulations, local watchlists and alerts, a paper portfolio,
and exports. Local browser storage is not a server account. Cross-venue matches
need verified settlement equivalence, not similar titles.

ML forecasts require the local service and a qualifying live champion. They
are separate from the terminal's transparent heuristic calculation. The first
real-data candidate failed the baseline and independent-event coverage gates;
it is not deployed as a live model. Generic news tone is not converted into an
outcome-specific probability. No claim of improved predictive accuracy or a
profitable strategy is made.

Paid billing, subscriber accounts, email delivery, background user alerts and
team access are not implemented. Pricing is shown as planned product pricing.
Without a signup endpoint the public form explains that signup is unavailable;
the local service can persist requests but does not send emails.

## Validation

```sh
python -m pip install -r MLModel/requirements.txt
(cd Backend && python -m unittest discover -s tests -v)
(cd MLModel && python -m unittest discover -s tests -v)
python scripts/check_js.py
python scripts/build_site.py
```

78 Python tests passed locally after repository integration. JavaScript syntax
checks and all ten terminal views were exercised in the browser. The CI workflow
repeats the Python and JavaScript checks. See [WORKSPACE.md](WORKSPACE.md) for
model assumptions and limitations; repository paths use root website files and
`terminal.html` in place of the Desktop `Website/` and `Prototype/` folders.

GitHub Pages workflow reference:
https://docs.github.com/en/pages/getting-started-with-github-pages/using-custom-workflows-with-github-pages
