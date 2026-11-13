"""Tests for entities.py. Run with:

    python -m unittest discover -s contrib -p "test_*.py"
"""

import datetime
import io
import ipaddress
import json
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path

import entities as entities_mod

TAGS = """
[tags.tx-spy]
description = "Listens for transaction announcements."
[tags.mass-connector]
description = "Connects to all nodes at once."
"""

ALPHA = """
name = "Alpha <b>&</b> Co"
summary = "Summary with <html> & escaping."
added = 2024-01-01
tags = ["tx-spy", "mass-connector"]
user_agents = ["/alpha:0.1/"]
description = \"\"\"
First paragraph with `code <b>` and https://example.com/a.

- one
- two
  continued
\"\"\"

[[references]]
url = "https://example.com/ref?a=1&b=2"
title = "Ref <title>"
date = 2024-01-02

[[networks]]
cidr = "192.0.2.0/24"
note = "Range note"

[[networks]]
cidr = "2001:db8::1"

[[networks]]
cidr = "198.51.100.0/24"
removed = 2024-06-01
note = "Retired"
"""

GONE = """
name = "Gone"
summary = "Entity that stopped connecting."
added = 2023-01-01
inactive = 2024-01-01
inactive_reason = "Stopped connecting."
tags = []
description = "Was here."

[[networks]]
cidr = "203.0.113.0/24"
removed = 2024-01-01
"""


class DatasetDir:
    """Writes a dataset into a temporary directory."""

    def __init__(self, entities: dict[str, str], tags: str = TAGS, sidecars: dict[str, str] | None = None):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.entities = self.root / "entities"
        self.entities.mkdir()
        self.tags = self.root / "tags.toml"
        self.tags.write_text(tags, encoding="utf-8")
        for slug, text in entities.items():
            (self.entities / f"{slug}.toml").write_text(text, encoding="utf-8")
        for slug, text in (sidecars or {}).items():
            (self.entities / f"{slug}{entities_mod.SIDECAR_SUFFIX}").write_text(text, encoding="utf-8")

    def load(self) -> entities_mod.Dataset:
        with redirect_stderr(io.StringIO()):
            return entities_mod.load_dataset(self.entities, self.tags)

    def errors(self) -> list[str]:
        with self.assert_raises() as e:
            self.load()
        return e.errors

    def assert_raises(self):
        return _Raises()

    def cleanup(self):
        self.tmp.cleanup()


class _Raises:
    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        if exc_type is None:
            raise AssertionError("expected ValidationError")
        if not issubclass(exc_type, entities_mod.ValidationError):
            return False
        self.errors = exc.errors
        return True


class FormatTests(unittest.TestCase):
    def test_inline_escapes_html(self):
        self.assertEqual(entities_mod.format_inline("a <b> & c"), "a &lt;b&gt; &amp; c")

    def test_inline_code_span(self):
        self.assertEqual(entities_mod.format_inline("x `a<b>` y"), "x <code>a&lt;b&gt;</code> y")

    def test_inline_does_not_autolink_inside_code(self):
        self.assertEqual(
            entities_mod.format_inline("`https://example.com`"), "<code>https://example.com</code>"
        )

    def test_inline_autolinks_and_keeps_trailing_punctuation(self):
        self.assertEqual(
            entities_mod.format_inline("see https://example.com/x)."),
            'see <a href="https://example.com/x">https://example.com/x</a>).',
        )

    def test_text_paragraphs_and_lists(self):
        html = entities_mod.format_text("Para one\nwraps.\n\n- a\n- b\n  more\n\nPara two.")
        self.assertEqual(
            html,
            "<p>Para one wraps.</p>\n<ul>\n<li>a</li>\n<li>b more</li>\n</ul>\n<p>Para two.</p>",
        )


class NetworkTests(unittest.TestCase):
    def test_text_strips_prefix_of_single_hosts(self):
        d = datetime.date(2024, 1, 1)
        self.assertEqual(entities_mod.Network(ipaddress.ip_network("10.0.0.1/32"), d).text, "10.0.0.1")
        self.assertEqual(entities_mod.Network(ipaddress.ip_network("2001:db8::1"), d).text, "2001:db8::1")
        self.assertEqual(entities_mod.Network(ipaddress.ip_network("10.0.0.0/24"), d).text, "10.0.0.0/24")


class LoadTests(unittest.TestCase):
    def setUp(self):
        self.dirs: list[DatasetDir] = []

    def tearDown(self):
        for d in self.dirs:
            d.cleanup()

    def dataset(self, entities, **kwargs) -> DatasetDir:
        d = DatasetDir(entities, **kwargs)
        self.dirs.append(d)
        return d

    def test_valid_dataset(self):
        ds = self.dataset(
            {"alpha": ALPHA, "gone": GONE}, sidecars={"alpha": "# comment\n\n192.0.2.0/25 # dup-free\n2001:db8::2\n"}
        ).load()
        self.assertEqual([e.slug for e in ds.entities], ["gone", "alpha"])  # sorted by added
        alpha = ds.entities[1]
        self.assertEqual(alpha.name, "Alpha <b>&</b> Co")
        self.assertEqual(len(alpha.networks), 5)
        self.assertEqual(len(alpha.active_networks), 4)
        self.assertEqual(len(alpha.historic_networks), 1)
        self.assertEqual(alpha.references[0].date, datetime.date(2024, 1, 2))
        sidecar = [n for n in alpha.networks if str(n.cidr) == "2001:db8::2/128"][0]
        self.assertEqual(sidecar.added, alpha.added)
        self.assertFalse(ds.entities[0].active)
        self.assertEqual(ds.entities[0].inactive, datetime.date(2024, 1, 1))
        self.assertEqual(ds.tags, {"tx-spy": "Listens for transaction announcements.", "mass-connector": "Connects to all nodes at once."})

    def test_errors_are_collected(self):
        broken = """
name = "Broken"
summary = "x"
added = 2024-01-01
inactive = 2023-01-01
inactive_reason = "r"
tags = ["nope"]
bantime_days = 90
bogus = 1
description = "d"
[[networks]]
cidr = "10.0.0.1/24"
[[networks]]
cidr = "10.1.0.0/24"
removed = 2023-12-31
[[references]]
url = "ftp://x"
title = "t"
"""
        errors = "\n".join(self.dataset({"broken": broken}).errors())
        for needle in (
            "unknown key 'bogus'",
            "unknown key 'bantime_days'",  # dropped from the schema
            "unknown tag 'nope'",
            "'inactive' must not be before 'added'",
            "has host bits set",
            "networks[1]: 'removed' must not be before 'added'",
            "'url' must be an http(s) URL",
        ):
            self.assertIn(needle, errors)

    def test_missing_required_key_and_wrong_type(self):
        errors = "\n".join(self.dataset({"a": 'name = "A"\nadded = "2024-01-01"\ntags = []\ndescription = "d"\n'}).errors())
        self.assertIn("missing required key 'summary'", errors)
        self.assertIn("'added' must be of type date", errors)

    def test_bad_slug_and_stray_files(self):
        d = self.dataset({"Bad_Slug": ALPHA})
        (d.entities / "orphan.ips.txt").write_text("", encoding="utf-8")
        (d.entities / "notes.txt").write_text("", encoding="utf-8")
        errors = "\n".join(d.errors())
        self.assertIn("file name must match", errors)
        self.assertIn("sidecar without a matching orphan.toml", errors)
        self.assertIn("unexpected file in entities directory", errors)

    def test_duplicates_across_entities(self):
        beta = ALPHA.replace('name = "Alpha <b>&</b> Co"', 'name = "alpha <B>&</B> co"')
        errors = "\n".join(self.dataset({"alpha": ALPHA, "beta": beta}).errors())
        self.assertIn("name 'alpha <B>&</B> co' is already used by alpha", errors)
        self.assertIn("network '192.0.2.0/24' is already listed by alpha", errors)

    def test_duplicate_within_entity_and_sidecar(self):
        errors = "\n".join(self.dataset({"alpha": ALPHA}, sidecars={"alpha": "192.0.2.0/24\nnot-an-ip\n"}).errors())
        self.assertIn("duplicate network '192.0.2.0/24'", errors)
        self.assertIn("alpha.ips.txt:2: invalid network 'not-an-ip'", errors)

    def test_active_entity_needs_a_network(self):
        text = GONE.replace("inactive = 2024-01-01\ninactive_reason = \"Stopped connecting.\"\n", "")
        errors = "\n".join(self.dataset({"gone": text}).errors())
        self.assertIn("an active entity needs at least one network in use", errors)

    def test_inactive_reason_requires_inactive(self):
        text = ALPHA.replace('added = 2024-01-01', 'added = 2024-01-01\ninactive_reason = "why"')
        errors = "\n".join(self.dataset({"alpha": text}).errors())
        self.assertIn("'inactive_reason' requires 'inactive'", errors)

    def test_tags_file_problems(self):
        errors = "\n".join(self.dataset({"alpha": ALPHA}, tags="[tags.Bad]\ndescription = 1\n[other]\n").errors())
        self.assertIn("tag names must match", errors)
        self.assertIn("'description' must be of type str", errors)
        self.assertIn("unknown top-level key 'other'", errors)
        d = self.dataset({"alpha": ALPHA})
        d.tags.unlink()
        self.assertIn("file not found", "\n".join(d.errors()))

    def test_overlap_is_a_warning_not_an_error(self):
        beta = GONE.replace('name = "Gone"', 'name = "Beta"').replace("203.0.113.0/24", "192.0.2.128/25")
        beta = beta.replace("inactive = 2024-01-01\ninactive_reason = \"Stopped connecting.\"\n", "").replace("removed = 2024-01-01\n", "")
        d = self.dataset({"alpha": ALPHA, "beta": beta})
        stderr = io.StringIO()
        with redirect_stderr(stderr):
            ds = entities_mod.load_dataset(d.entities, d.tags)
        self.assertEqual(len(ds.entities), 2)
        self.assertIn("overlaps", stderr.getvalue())


class OutputTests(unittest.TestCase):
    def setUp(self):
        self.d = DatasetDir({"alpha": ALPHA, "gone": GONE})
        self.ds = self.d.load()

    def tearDown(self):
        self.d.cleanup()

    def test_json_export_matches_schema_keys(self):
        data = entities_mod.dataset_to_json(self.ds)
        schema = entities_mod.JSON_SCHEMA
        self.assertEqual(set(data) - {"$schema"}, set(schema["required"]))
        entity_props = schema["$defs"]["entity"]["properties"]
        network_props = schema["$defs"]["network"]["properties"]
        for entity in data["entities"]:
            self.assertEqual(set(entity), set(entity_props))
            for network in entity["networks"]:
                self.assertEqual(set(network), set(network_props))
        alpha = [e for e in data["entities"] if e["slug"] == "alpha"][0]
        self.assertEqual(alpha["networks"][1]["cidr"], "2001:db8::1")
        self.assertFalse(alpha["networks"][2]["active"])
        gone = [e for e in data["entities"] if e["slug"] == "gone"][0]
        self.assertEqual(gone["inactive"], "2024-01-01")
        self.assertFalse(gone["active"])
        json.dumps(data)  # serialisable

    def test_json_export_has_no_ban_fields(self):
        data = entities_mod.dataset_to_json(self.ds)
        self.assertNotIn("default_bantime_days", data)
        for entity in data["entities"]:
            self.assertNotIn("bantime_days", entity)

    def test_banlist_data_only_has_active_entities_and_networks(self):
        data = entities_mod.banlist_data(self.ds)
        self.assertEqual([e["slug"] for e in data["entities"]], ["alpha"])
        self.assertEqual(data["entities"][0]["networks"], ["192.0.2.0/24", "2001:db8::1"])
        self.assertEqual(data["entities"][0]["tags"], ["tx-spy", "mass-connector"])

    def test_site_build(self):
        with tempfile.TemporaryDirectory() as out:
            outdir = Path(out)
            with redirect_stdout(io.StringIO()):
                entities_mod.write_site(self.ds, outdir)
            for name in ("index.html", "style.css", "banlist.js", "theme.js", ".nojekyll", "entities.json",
                         "schema.json", "banlist/index.html", "tags/index.html",
                         "alpha/index.html", "gone/index.html"):
                self.assertTrue((outdir / name).exists(), name)
            index = (outdir / "index.html").read_text(encoding="utf-8")
            self.assertIn('<a href="alpha/">Alpha &lt;b&gt;&amp;&lt;/b&gt; Co</a>', index)
            self.assertIn("No longer observed", index)
            self.assertIn('href="tags/#tag-mass-connector"', index)
            self.assertIn('id="theme-toggle"', index)
            self.assertIn("Rotate the cards once a day", index)
            self.assertIn('src="./theme.js"', index)
            self.assertNotIn("<b>&</b>", index)
            self.assertNotIn("bitcoin-cli setban", index)
            page = (outdir / "alpha" / "index.html").read_text(encoding="utf-8")
            self.assertIn("<code>code &lt;b&gt;</code>", page)
            self.assertIn('<a href="https://example.com/ref?a=1&amp;b=2">Ref &lt;title&gt;</a>', page)
            self.assertIn("Previously used networks", page)
            self.assertNotIn("bitcoin-cli setban", page)
            gone = (outdir / "gone" / "index.html").read_text(encoding="utf-8")
            self.assertIn('class="status inactive"', gone)
            self.assertIn("Stopped connecting.", gone)

    def test_tags_page_lists_tags_with_their_entities(self):
        with tempfile.TemporaryDirectory() as out:
            outdir = Path(out)
            with redirect_stdout(io.StringIO()):
                entities_mod.write_site(self.ds, outdir)
            page = (outdir / "tags" / "index.html").read_text(encoding="utf-8")
            self.assertIn('<h2 id="tag-tx-spy">', page)
            self.assertIn("Listens for transaction announcements.", page)
            self.assertIn('1 entity: <a href="../alpha/">Alpha &lt;b&gt;&amp;&lt;/b&gt; Co</a>', page)
            # "gone" carries no tags, so every tag here has exactly one entity.
            self.assertNotIn("No entities carry this tag.", page)
            self.assertIn('href="../banlist/"', page)  # nav

    def test_banlist_page_embeds_data_and_escapes_it(self):
        with tempfile.TemporaryDirectory() as out:
            outdir = Path(out)
            with redirect_stdout(io.StringIO()):
                entities_mod.write_site(self.ds, outdir)
            page = (outdir / "banlist" / "index.html").read_text(encoding="utf-8")
            blob = page.split('<script type="application/json" id="entity-data">')[1].split("</script>")[0]
            data = json.loads(blob.replace("<\\/", "</"))
            self.assertEqual([e["slug"] for e in data["entities"]], ["alpha"])
            self.assertEqual(data["entities"][0]["name"], "Alpha <b>&</b> Co")
            self.assertNotIn("</", blob)  # cannot close the script element
            self.assertIn('class="entity-toggle"', page)
            self.assertIn('value="tx-spy"', page)
            self.assertIn('src="../banlist.js"', page)


class CliTests(unittest.TestCase):
    def test_validate_exit_codes(self):
        good = DatasetDir({"alpha": ALPHA})
        bad = DatasetDir({"alpha": ALPHA.replace('tags = ["tx-spy", "mass-connector"]', 'tags = ["nope"]')})
        try:
            with redirect_stdout(io.StringIO()), redirect_stderr(io.StringIO()):
                self.assertEqual(entities_mod.main(["--entities", str(good.entities), "--tags", str(good.tags), "validate"], self_test=False), 0)
                self.assertEqual(entities_mod.main(["--entities", str(bad.entities), "--tags", str(bad.tags), "validate"], self_test=False), 1)
        finally:
            good.cleanup()
            bad.cleanup()

    def test_site_command_writes_files(self):
        d = DatasetDir({"alpha": ALPHA})
        try:
            with tempfile.TemporaryDirectory() as out, redirect_stdout(io.StringIO()), redirect_stderr(io.StringIO()):
                code = entities_mod.main(["--entities", str(d.entities), "--tags", str(d.tags), "site", out], self_test=False)
                self.assertEqual(code, 0)
                self.assertTrue((Path(out) / "banlist" / "index.html").exists())
                self.assertTrue((Path(out) / "entities.json").exists())
        finally:
            d.cleanup()


if __name__ == "__main__":
    unittest.main()
