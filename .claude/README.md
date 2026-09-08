# Claude configuration

Project-level Claude Code settings for this repository.

## Watermarks Remover plugin

`settings.json` registers [guillaumemeyer/watermarks-remover](https://github.com/guillaumemeyer/watermarks-remover)
as a plugin marketplace and enables its plugin, so any Claude Code session
opened in this repo gets the AI-provenance-mark tooling automatically. Claude
Code asks you to trust the folder the first time; after that the marketplace is
added and the plugin installed with no further prompt.

The plugin is pinned to tag **v0.7.0** rather than tracking `main`, because it
registers a `PostToolUse` hook that executes code from the upstream repo on
every file Claude writes. To move to a newer release, bump `ref` in
`settings.json` and re-run `/plugin marketplace update watermarks-remover`.

One upstream quirk to expect: the `v0.7.0` tag ships manifests that still
self-report version `0.5.0` (bumped on `main` five days after the tag), so
`/plugin` will show 0.5.0. The skills and the hook at that tag are byte-identical
to `main` - only the version string differs. Both manifests pass
`claude plugin validate --strict` at the pinned tag.

### What it adds

| Skill | Invoke | Needs |
| --- | --- | --- |
| `remove-ai-marks` | `/watermarks-remover:remove-ai-marks` (or `/remove-ai-marks`) | the HTTP service below |
| `clean-user-facing-text` | `/watermarks-remover:clean-user-facing-text` | nothing — self-contained, text only |

It also installs a `PostToolUse` hook on `Write|Edit|MultiEdit|NotebookEdit`.
Default mode is `check`: it reports invisible Unicode / C2PA / AI metadata in
files Claude just wrote and leaves them alone. Switch it to strip in place with
**Hook mode** in `/plugin manage`, or `WATERMARKS_HOOK_MODE=clean` in the
environment.

### Running the service

`remove-ai-marks` is a thin HTTP client — it ships no cleaning code and refuses
to fall back to local cleaning. Start the service before using it. Python 3.10+
stdlib only, no dependencies:

```bash
git clone https://github.com/guillaumemeyer/watermarks-remover
cd watermarks-remover && make serve        # http://127.0.0.1:8765
```

Or without a clone:

```bash
docker run --rm -p 127.0.0.1:8765:8765 --read-only --tmpfs /tmp \
  ghcr.io/guillaumemeyer/watermarks-remover:latest
```

Check it with `curl -sf http://127.0.0.1:8765/health`. If you run it anywhere
else, set `WATERMARKS_SERVICE_URL`.

Image and container metadata cleaning works out of the box. Text cleaning
(`POST /clean` on `.txt`) additionally requires Layer B rewrite configuration
(`WATERMARKS_REWRITE_*`, or `transformers` + `roberta-large`) or the service
returns HTTP 400 — see the upstream README.

`clean-user-facing-text` needs none of this and is the one to reach for when
you only want a Unicode audit and a prose pass on site copy.

### Auditing the site

The upstream checkout also carries an aggregate auditor, useful before a
deploy:

```bash
python3 /path/to/watermarks-remover/service/scripts/audit_dir.py . --json
```

Exit codes: `0` clean, `1` actionable findings, `2` usage error, `3` partial
scan (inconclusive, not clean). As of this commit the site's 11 tracked files
audit clean — no C2PA, no AI metadata, no suspicious codepoints.

### Scope

Intended for hygiene on content you own. The deterministic layers (invisible
Unicode, C2PA/EXIF/XMP metadata) remove what they report; the statistical
rewrite layer is best-effort and never proves human authorship. Upstream is MIT
licensed.
