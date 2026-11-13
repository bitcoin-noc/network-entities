# entities.py

Validates the dataset in `entities/` and generates the static website. Requires
Python 3.11 or newer and no third-party packages.

```
python contrib/entities.py validate
python contrib/entities.py site OUTDIR
```

- Every invocation first runs the unit tests in `test_entities.py` and stops if
  they fail.
- `validate` loads `tags.toml` and every `entities/*.toml` (plus `*.ips.txt`
  sidecars), reports all schema errors, and exits non-zero if there are any.
- `site` writes `index.html`, one `<slug>/index.html` per entity,
  `banlist/index.html` (the banlist generator), `entities.json`, `schema.json`,
  `style.css` and `banlist.js`.

The generator page is plain JavaScript with no dependencies. The networks of the
active entities are embedded in the page as JSON, so it needs no fetch and works
offline; `banlist.js` only turns the reader's selection into `setban` commands.
The repository publishes no ready-made banlist.

Templates live in `templates/` (Python `string.Template` syntax), the stylesheet
and script in `static/`.
