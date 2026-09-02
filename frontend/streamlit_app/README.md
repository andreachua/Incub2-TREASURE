# My Assets Record — operator front end

A Streamlit port of the exported design bundle in
`../My Assets Record (MAR)_Project Assets/`. Same three screens, same layout,
type, colour and workflow; the values come from the pipeline's API instead of
the design's placeholders, and the source documents behind a record can be
opened.

## Where the design lives

The export is a 6.7 MB self-extracting bundle: a gzip+base64 manifest of fonts,
React 18 UMD, a `dc-runtime` template engine and the PRIZM 4.0 design system,
plus a JSON-encoded `__bundler/template` holding the real document.

`tools/extract_bundle.py` decodes it into reference material this port is
maintained against:

```
python tools/extract_bundle.py
```

| Output | What it is |
|---|---|
| `design/template.html` | the decoded document (PRIZM `<style>`, markup, the `DCLogic` class) |
| `design/markup.html` | the markup alone, with the export's aliases undone (`sc-raw-td` → `td`, `sc-camel-view-box` → `viewBox`) |
| `design/logic.js` | the design's component class — the workflow this app reproduces |
| `styles/prizm.css` | `colors_and_type.css`, unchanged except `@font-face` srcs repointed at `app/static/fonts/` |
| `static/fonts/*.ttf` | the seven weights the design actually renders (Inter 400/500/600/700, JetBrains Mono 400/500/600) |

`design/` is reference only and is excluded from the image.

## How fidelity is kept

The design is 383 inline `style` attributes over raw `div`/`span` with PRIZM
custom properties and **no CSS classes at all**. So:

- Everything read-only — header, home cards, the legend, the rails, the assets
  table, the toast — is the design's own markup, emitted verbatim through
  `st.markdown(unsafe_allow_html=True)`.
- Everything an operator clicks or types into is a real Streamlit widget.
  `styles/app.css` lays those out inside that markup (keyed on the `.st-key-*`
  class Streamlit puts on a container) and gives them the design's control spec.
- Cards that are buttons in the design keep their markup and get an invisible
  Streamlit button laid over them, so the whole card is the click target.
- The design's five JavaScript behaviours are reproduced in CSS: the `(i)`
  tooltips, the hover "WHY" rationale popover, the description counter, the
  toast's 7-second auto-dismiss, and the assets table's expand/collapse
  (a visually hidden checkbox beside the table plus a `#id:checked ~ table …`
  rule emitted per row — no `:has()`, no script).

Two deliberate differences, both noted in the code:

- The search box refilters on Enter/blur rather than the design's 250 ms
  debounce — Streamlit reruns per interaction.
- The home screen's empty state carries the upload control. The design only
  ever shows that section with a record waiting, and its upload affordance
  lives on the review screen's rail — which an empty register cannot reach, and
  which is read-only here anyway: the rail lists source documents but does not
  take them.

## Running

```bash
# with the rest of the stack
docker compose up -d --build          # http://127.0.0.1:8501

# on its own, against a local API
API_BASE=http://localhost:8000 streamlit run app.py
```

Under compose this directory is bind-mounted over the image's copy of it
(`./frontend/streamlit_app:/app:ro`) with `runOnSave` on and the polling file
watcher, so a Python edit on the host reruns the app in the browser — no
rebuild, no restart. A stylesheet edit needs a browser refresh rather than a
rerun: `_styles()` re-reads both CSS files on every run, but Streamlit's watcher
only follows the imported Python modules. Rebuild (`--build`) only when
`requirements.txt` or the Dockerfile changes.

`API_BASE` is a server-side URL: Streamlit renders on the server and calls the
API itself, so under compose it is `http://api:8000` and the browser only ever
talks to this container. There is no CORS in the picture.

## Layout

```
app.py                 page config, styles, screen state, header, toast
api.py                 the backend client (cached reads, cleared after a write)
ui.py                  icons and shared markup, copied from the design
screens/home.py        the design's onHome block   — GET /api/summary
screens/review.py      the design's onForm block   — GET /api/review/…
screens/assets.py      the design's onAssets block — GET /api/assets
viewer.py              the source-document modal   — GET /api/documents/…
styles/app.css         Streamlit resets, the design's layout, the CSS behaviours
tools/extract_bundle.py  decodes the design export into design/ and styles/
```
