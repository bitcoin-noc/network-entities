#!/usr/bin/env python3
"""Tooling for the Bitcoin network entities dataset.

Validates the entity files in entities/ and generates the static website.
Uses only the Python standard library and requires Python 3.11 or newer (for
tomllib).

    python contrib/entities.py validate
    python contrib/entities.py site OUTDIR
"""

import argparse
import datetime
import html
import ipaddress
import json
import re
import shutil
import string
import sys
import tomllib
from dataclasses import dataclass, field
from pathlib import Path

REPO_URL = "https://github.com/bitcoin-noc/network-entities"
SITE_URL = "https://bitcoin-noc.github.io/network-entities/"
SITE_TITLE = "Bitcoin network entities"
SITE_DESCRIPTION = (
    "Unusual, misbehaving, and possibly malicious entities on the Bitcoin "
    "peer-to-peer network, including some that appear to spy on or track its users."
)

# Only the default of the duration input on the banlist generator. Ban durations
# are not part of the dataset.
DEFAULT_BANTIME_DAYS = 365
SECONDS_PER_DAY = 60 * 60 * 24

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
TEMPLATES_DIR = HERE / "templates"
STATIC_DIR = HERE / "static"
DEFAULT_ENTITIES_DIR = ROOT / "entities"
DEFAULT_TAGS_FILE = ROOT / "tags.toml"

SLUG_RE = re.compile(r"^[a-z0-9][a-z0-9-]*$")
SIDECAR_SUFFIX = ".ips.txt"

OUTPUT_JSON = "entities.json"
OUTPUT_SCHEMA = "schema.json"
BANLIST_DIR = "banlist"
TAGS_DIR = "tags"

Date = datetime.date
IPNetwork = ipaddress.IPv4Network | ipaddress.IPv6Network


# --------------------------------------------------------------------------
# Data model
# --------------------------------------------------------------------------


@dataclass
class Network:
    cidr: IPNetwork
    added: Date
    removed: Date | None = None
    note: str | None = None

    @property
    def active(self) -> bool:
        return self.removed is None

    @property
    def text(self) -> str:
        """The network as written out: a bare address for single hosts."""
        if self.cidr.prefixlen == self.cidr.max_prefixlen:
            return str(self.cidr.network_address)
        return str(self.cidr)


@dataclass
class Reference:
    url: str
    title: str
    date: Date | None = None


@dataclass
class Entity:
    slug: str
    path: Path
    name: str
    summary: str
    added: Date
    tags: list[str]
    description: str
    inactive: Date | None = None
    inactive_reason: str | None = None
    user_agents: list[str] = field(default_factory=list)
    references: list[Reference] = field(default_factory=list)
    networks: list[Network] = field(default_factory=list)

    @property
    def active(self) -> bool:
        """True while the entity is still observed on the network."""
        return self.inactive is None

    @property
    def active_networks(self) -> list[Network]:
        return [n for n in self.networks if n.active]

    @property
    def historic_networks(self) -> list[Network]:
        return [n for n in self.networks if not n.active]


@dataclass
class Dataset:
    entities: list[Entity]
    tags: dict[str, str]  # tag -> description

    @property
    def active_entities(self) -> list[Entity]:
        return [e for e in self.entities if e.active]


class ValidationError(Exception):
    def __init__(self, errors: list[str]):
        super().__init__("\n".join(errors))
        self.errors = errors


# --------------------------------------------------------------------------
# Loading and validation
# --------------------------------------------------------------------------

# Field specs: key -> (type, required). Types: "str", "date", "int", "list[str]",
# "list[table]", "str|None".
ENTITY_SPEC = {
    "name": ("str", True),
    "summary": ("str", True),
    "added": ("date", True),
    "tags": ("list[str]", True),
    "description": ("str", True),
    "inactive": ("date", False),
    "inactive_reason": ("str", False),
    "user_agents": ("list[str]", False),
    "references": ("list[table]", False),
    "networks": ("list[table]", False),
}
REFERENCE_SPEC = {"url": ("str", True), "title": ("str", True), "date": ("date", False)}
NETWORK_SPEC = {
    "cidr": ("str", True),
    "added": ("date", False),
    "removed": ("date", False),
    "note": ("str", False),
}


def _has_type(value, typ: str) -> bool:
    if typ == "str":
        return isinstance(value, str)
    if typ == "int":
        return type(value) is int  # bool is a subclass of int
    if typ == "date":
        return type(value) is datetime.date  # datetime is a subclass of date
    if typ == "list[str]":
        return isinstance(value, list) and all(isinstance(v, str) for v in value)
    if typ == "list[table]":
        return isinstance(value, list) and all(isinstance(v, dict) for v in value)
    raise ValueError(typ)


def _check_table(errors: list[str], ctx: str, table: dict, spec: dict) -> bool:
    """Checks keys and types of a TOML table.

    Returns True if the known keys are usable (unknown keys are reported but
    do not stop further checks)."""
    ok = True
    if not isinstance(table, dict):
        errors.append(f"{ctx}: expected a table")
        return False
    for key in table:
        if key not in spec:
            errors.append(f"{ctx}: unknown key '{key}'")
    for key, (typ, required) in spec.items():
        if key not in table:
            if required:
                errors.append(f"{ctx}: missing required key '{key}'")
                ok = False
            continue
        if not _has_type(table[key], typ):
            errors.append(f"{ctx}: '{key}' must be of type {typ}")
            ok = False
        elif typ == "str" and not table[key].strip():
            errors.append(f"{ctx}: '{key}' must not be empty")
            ok = False
    return ok


def _parse_cidr(errors: list[str], ctx: str, text: str) -> IPNetwork | None:
    try:
        return ipaddress.ip_network(text, strict=True)
    except ValueError as e:
        errors.append(f"{ctx}: invalid network '{text}' ({e})")
        return None


def load_tags(path: Path) -> dict[str, str]:
    errors: list[str] = []
    try:
        with open(path, "rb") as f:
            data = tomllib.load(f)
    except FileNotFoundError:
        raise ValidationError([f"{path}: file not found"])
    except tomllib.TOMLDecodeError as e:
        raise ValidationError([f"{path}: invalid TOML ({e})"])
    for key in data:
        if key != "tags":
            errors.append(f"{path}: unknown top-level key '{key}'")
    tags = data.get("tags", {})
    if not isinstance(tags, dict):
        errors.append(f"{path}: 'tags' must be a table")
        tags = {}
    result: dict[str, str] = {}
    for tag, table in tags.items():
        ctx = f"{path}: tag '{tag}'"
        if not SLUG_RE.match(tag):
            errors.append(f"{ctx}: tag names must match {SLUG_RE.pattern}")
        if _check_table(errors, ctx, table, {"description": ("str", True)}):
            result[tag] = table["description"].strip()
    if errors:
        raise ValidationError(errors)
    return result


def _read_sidecar(errors: list[str], path: Path, added: Date) -> list[Network]:
    networks = []
    with open(path, encoding="utf-8") as f:
        for lineno, line in enumerate(f, start=1):
            line = line.split("#", 1)[0].strip()
            if not line:
                continue
            cidr = _parse_cidr(errors, f"{path}:{lineno}", line)
            if cidr is not None:
                networks.append(Network(cidr=cidr, added=added))
    return networks


def _load_entity(errors: list[str], path: Path, tags: dict[str, str]) -> Entity | None:
    slug = path.name[: -len(".toml")]
    ctx = str(path)
    if not SLUG_RE.match(slug):
        errors.append(f"{ctx}: file name must match {SLUG_RE.pattern}.toml")
        return None
    try:
        with open(path, "rb") as f:
            data = tomllib.load(f)
    except tomllib.TOMLDecodeError as e:
        errors.append(f"{ctx}: invalid TOML ({e})")
        return None
    if not _check_table(errors, ctx, data, ENTITY_SPEC):
        return None

    entity = Entity(
        slug=slug,
        path=path,
        name=" ".join(data["name"].split()),
        summary=" ".join(data["summary"].split()),
        added=data["added"],
        tags=data["tags"],
        description=data["description"].strip(),
        inactive=data.get("inactive"),
        inactive_reason=data.get("inactive_reason"),
        user_agents=data.get("user_agents", []),
    )

    for tag in entity.tags:
        if tag not in tags:
            errors.append(f"{ctx}: unknown tag '{tag}' (define it in tags.toml)")
    if len(set(entity.tags)) != len(entity.tags):
        errors.append(f"{ctx}: duplicate tags")
    if entity.inactive is not None and entity.inactive < entity.added:
        errors.append(f"{ctx}: 'inactive' must not be before 'added'")
    if entity.inactive_reason is not None and entity.inactive is None:
        errors.append(f"{ctx}: 'inactive_reason' requires 'inactive'")
    for ua in entity.user_agents:
        if not ua.strip():
            errors.append(f"{ctx}: empty user agent")

    for i, table in enumerate(data.get("references", [])):
        rctx = f"{ctx}: references[{i}]"
        if _check_table(errors, rctx, table, REFERENCE_SPEC):
            url = table["url"].strip()
            if not re.match(r"^https?://\S+$", url):
                errors.append(f"{rctx}: 'url' must be an http(s) URL")
            entity.references.append(
                Reference(url=url, title=" ".join(table["title"].split()), date=table.get("date"))
            )

    for i, table in enumerate(data.get("networks", [])):
        nctx = f"{ctx}: networks[{i}]"
        if not _check_table(errors, nctx, table, NETWORK_SPEC):
            continue
        cidr = _parse_cidr(errors, nctx, table["cidr"].strip())
        if cidr is None:
            continue
        network = Network(
            cidr=cidr,
            added=table.get("added", entity.added),
            removed=table.get("removed"),
            note=" ".join(table["note"].split()) if "note" in table else None,
        )
        if network.removed is not None and network.removed < network.added:
            errors.append(f"{nctx}: 'removed' must not be before 'added'")
        entity.networks.append(network)

    sidecar = path.with_name(slug + SIDECAR_SUFFIX)
    if sidecar.exists():
        entity.networks.extend(_read_sidecar(errors, sidecar, entity.added))

    seen: set[IPNetwork] = set()
    for network in entity.networks:
        if network.cidr in seen:
            errors.append(f"{ctx}: duplicate network '{network.cidr}'")
        seen.add(network.cidr)
    if entity.active and not entity.active_networks:
        errors.append(f"{ctx}: an active entity needs at least one network in use")
    return entity


def load_dataset(entities_dir: Path, tags_file: Path) -> Dataset:
    """Loads and validates the whole dataset. Raises ValidationError."""
    errors: list[str] = []
    try:
        tags = load_tags(tags_file)
    except ValidationError as e:
        errors.extend(e.errors)
        tags = {}

    entities: list[Entity] = []
    if not entities_dir.is_dir():
        raise ValidationError([f"{entities_dir}: not a directory"])
    for path in sorted(entities_dir.iterdir()):
        if path.suffix == ".toml":
            entity = _load_entity(errors, path, tags)
            if entity is not None:
                entities.append(entity)
        elif path.name.endswith(SIDECAR_SUFFIX):
            owner = path.with_name(path.name[: -len(SIDECAR_SUFFIX)] + ".toml")
            if not owner.exists():
                errors.append(f"{path}: sidecar without a matching {owner.name}")
        else:
            errors.append(f"{path}: unexpected file in entities directory")

    names: dict[str, str] = {}
    owners: dict[IPNetwork, str] = {}
    for entity in entities:
        key = entity.name.casefold()
        if key in names:
            errors.append(f"{entity.path}: name '{entity.name}' is already used by {names[key]}")
        names[key] = entity.slug
        for network in entity.networks:
            if network.cidr in owners:
                errors.append(
                    f"{entity.path}: network '{network.cidr}' is already listed by {owners[network.cidr]}"
                )
            owners[network.cidr] = entity.slug

    # Overlapping (but not identical) networks across entities are only reported.
    # Two single hosts can only overlap by being identical, which is already an
    # error above, so only ranges need to be compared against everything else.
    all_networks = [(n.cidr, e.slug) for e in entities for n in e.networks]
    ranges = [(c, s) for c, s in all_networks if c.prefixlen < c.max_prefixlen]
    for a, slug_a in ranges:
        for b, slug_b in all_networks:
            if slug_a != slug_b and a.version == b.version and a != b and a.overlaps(b):
                print(f"warning: {slug_a} '{a}' overlaps {slug_b} '{b}'", file=sys.stderr)

    if errors:
        raise ValidationError(errors)
    entities.sort(key=lambda e: (e.added, e.slug))
    return Dataset(entities=entities, tags=tags)


# --------------------------------------------------------------------------
# Text formatting (plain text -> HTML)
# --------------------------------------------------------------------------

CODE_RE = re.compile(r"`([^`\n]+)`")
URL_RE = re.compile(r"https?://[^\s<>\"']+")
URL_TRAILING = ".,;:)]}'\""


def _autolink(text: str) -> str:
    out = []
    pos = 0
    for m in URL_RE.finditer(text):
        out.append(html.escape(text[pos : m.start()]))
        url = m.group(0)
        trail = ""
        while url and url[-1] in URL_TRAILING:
            trail = url[-1] + trail
            url = url[:-1]
        out.append(f'<a href="{html.escape(url, quote=True)}">{html.escape(url)}</a>')
        out.append(html.escape(trail))
        pos = m.end()
    out.append(html.escape(text[pos:]))
    return "".join(out)


def format_inline(text: str) -> str:
    """Escapes text; `code` spans become <code>, bare URLs become links."""
    parts = CODE_RE.split(text)
    out = []
    for i, part in enumerate(parts):
        if i % 2 == 1:
            out.append(f"<code>{html.escape(part)}</code>")
        else:
            out.append(_autolink(part))
    return "".join(out)


def format_text(text: str) -> str:
    """Formats plain text: paragraphs separated by blank lines, '- ' bullet lists."""
    blocks = re.split(r"\n[ \t]*\n", text.strip())
    out = []
    for block in blocks:
        lines = [line.rstrip() for line in block.split("\n") if line.strip()]
        if not lines:
            continue
        if lines[0].startswith("- "):
            items: list[str] = []
            for line in lines:
                if line.startswith("- "):
                    items.append(line[2:].strip())
                elif items:
                    items[-1] += " " + line.strip()
            out.append("<ul>\n" + "".join(f"<li>{format_inline(i)}</li>\n" for i in items) + "</ul>")
        else:
            out.append(f"<p>{format_inline(' '.join(line.strip() for line in lines))}</p>")
    return "\n".join(out)


# --------------------------------------------------------------------------
# JSON export and schema
# --------------------------------------------------------------------------


def _iso(d: Date | None) -> str | None:
    return d.isoformat() if d is not None else None


def entity_to_json(entity: Entity) -> dict:
    return {
        "slug": entity.slug,
        "name": entity.name,
        "summary": entity.summary,
        "added": _iso(entity.added),
        "inactive": _iso(entity.inactive),
        "inactive_reason": entity.inactive_reason,
        "active": entity.active,
        "tags": entity.tags,
        "user_agents": entity.user_agents,
        "references": [
            {"url": r.url, "title": r.title, "date": _iso(r.date)} for r in entity.references
        ],
        "description": entity.description,
        "networks": [
            {
                "cidr": n.text,
                "added": _iso(n.added),
                "removed": _iso(n.removed),
                "note": n.note,
                "active": n.active,
            }
            for n in entity.networks
        ],
    }


def dataset_to_json(dataset: Dataset) -> dict:
    return {
        "$schema": SITE_URL + OUTPUT_SCHEMA,
        "generated": datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "source": REPO_URL,
        "tags": [{"tag": t, "description": d} for t, d in dataset.tags.items()],
        "entities": [entity_to_json(e) for e in dataset.entities],
    }


_DATE = {"type": "string", "format": "date", "pattern": r"^\d{4}-\d{2}-\d{2}$"}
_NULLABLE_DATE = {"oneOf": [_DATE, {"type": "null"}]}
_NULLABLE_STR = {"type": ["string", "null"]}

JSON_SCHEMA = {
    "$schema": "https://json-schema.org/draft/2020-12/schema",
    "$id": SITE_URL + OUTPUT_SCHEMA,
    "title": SITE_TITLE,
    "description": "Entities on the Bitcoin peer-to-peer network documented on " + SITE_URL,
    "type": "object",
    "required": ["generated", "source", "tags", "entities"],
    "additionalProperties": False,
    "properties": {
        "$schema": {"type": "string"},
        "generated": {"type": "string", "format": "date-time"},
        "source": {"type": "string", "format": "uri"},
        "tags": {
            "type": "array",
            "items": {
                "type": "object",
                "required": ["tag", "description"],
                "additionalProperties": False,
                "properties": {
                    "tag": {"type": "string", "pattern": SLUG_RE.pattern},
                    "description": {"type": "string"},
                },
            },
        },
        "entities": {"type": "array", "items": {"$ref": "#/$defs/entity"}},
    },
    "$defs": {
        "entity": {
            "type": "object",
            "required": [
                "slug", "name", "summary", "added", "inactive", "inactive_reason", "active",
                "tags", "user_agents", "references", "description", "networks",
            ],
            "additionalProperties": False,
            "properties": {
                "slug": {"type": "string", "pattern": SLUG_RE.pattern},
                "name": {"type": "string"},
                "summary": {"type": "string"},
                "added": _DATE,
                "inactive": dict(
                    _NULLABLE_DATE,
                    description="Set once the entity is no longer observed on the network.",
                ),
                "inactive_reason": _NULLABLE_STR,
                "active": {
                    "type": "boolean",
                    "description": "False once the entity is no longer observed on the network.",
                },
                "tags": {"type": "array", "items": {"type": "string"}},
                "user_agents": {"type": "array", "items": {"type": "string"}},
                "references": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "required": ["url", "title", "date"],
                        "additionalProperties": False,
                        "properties": {
                            "url": {"type": "string", "format": "uri"},
                            "title": {"type": "string"},
                            "date": _NULLABLE_DATE,
                        },
                    },
                },
                "description": {"type": "string"},
                "networks": {"type": "array", "items": {"$ref": "#/$defs/network"}},
            },
        },
        "network": {
            "type": "object",
            "required": ["cidr", "added", "removed", "note", "active"],
            "additionalProperties": False,
            "properties": {
                "cidr": {
                    "type": "string",
                    "description": "IPv4/IPv6 address or CIDR network. Single hosts are written without a prefix length.",
                },
                "added": _DATE,
                "removed": _NULLABLE_DATE,
                "note": _NULLABLE_STR,
                "active": {
                    "type": "boolean",
                    "description": "False once the entity stopped using the network.",
                },
            },
        },
    },
}


def write_exports(dataset: Dataset, outdir: Path) -> None:
    (outdir / OUTPUT_JSON).write_text(
        json.dumps(dataset_to_json(dataset), indent=2) + "\n", encoding="utf-8"
    )
    (outdir / OUTPUT_SCHEMA).write_text(json.dumps(JSON_SCHEMA, indent=2) + "\n", encoding="utf-8")
    print(f"wrote {outdir / OUTPUT_JSON} and {OUTPUT_SCHEMA}")


# --------------------------------------------------------------------------
# Website
# --------------------------------------------------------------------------


def _template(name: str) -> string.Template:
    return string.Template((TEMPLATES_DIR / name).read_text(encoding="utf-8"))


def _e(text) -> str:
    return html.escape(str(text), quote=True)


def _plural(n: int, singular: str, plural: str) -> str:
    return f"{n} {singular if n == 1 else plural}"


def _chips(tags: list[str], root: str) -> str:
    return " ".join(
        f'<a class="tag" href="{root}{TAGS_DIR}/#tag-{_e(t)}">{_e(t)}</a>' for t in tags
    )


def _networks_table(networks: list[Network], historic: bool) -> str:
    head = "<th>Network</th><th>First seen</th>" + ("<th>Last seen</th>" if historic else "") + "<th>Note</th>"
    rows = []
    for n in networks:
        cells = [f"<td><code>{_e(n.text)}</code></td>", f"<td>{_e(n.added)}</td>"]
        if historic:
            cells.append(f"<td>{_e(n.removed)}</td>")
        cells.append(f"<td>{format_inline(n.note) if n.note else ''}</td>")
        rows.append("<tr>" + "".join(cells) + "</tr>")
    return f'<div class="table-wrap"><table><thead><tr>{head}</tr></thead><tbody>\n' + "\n".join(rows) + "\n</tbody></table></div>"


def render_page(
    title: str,
    description: str,
    canonical: str,
    root: str,
    content: str,
    scripts: str = "",
    brand_is_heading: bool = False,
) -> str:
    # The index has no heading of its own, so there the site title in the
    # masthead is the page's <h1>.
    tag = "h1" if brand_is_heading else "p"
    brand = f'<{tag} class="brand"><a href="{root}">{_e(SITE_TITLE)}</a></{tag}>'
    return _template("base.html").substitute(
        title=_e(title),
        site_title=_e(SITE_TITLE),
        description=_e(description),
        canonical=_e(canonical),
        root=root,
        repo_url=_e(REPO_URL),
        banlist_dir=BANLIST_DIR,
        tags_dir=TAGS_DIR,
        brand=brand,
        content=content,
        scripts=scripts,
    )


def render_index(dataset: Dataset) -> str:
    active = sorted(dataset.active_entities, key=lambda e: (e.added, e.slug), reverse=True)
    historic = sorted([e for e in dataset.entities if not e.active], key=lambda e: e.inactive, reverse=True)

    def card(e: Entity) -> str:
        chips = f'<p class="chips">{_chips(e.tags, "")}</p>' if e.tags else ""
        return (
            '<article class="entity">'
            f'<h3><a href="{_e(e.slug)}/">{_e(e.name)}</a></h3>'
            f"<p>{_e(e.summary)}</p>"
            f"{chips}"
            "</article>"
        )

    entity_cards = "\n".join(card(e) for e in active) or "<p>No entities yet.</p>"

    if historic:
        historic_rows = "\n".join(
            "<tr>"
            f'<td><a href="{_e(e.slug)}/">{_e(e.name)}</a></td>'
            f"<td>{_e(e.added)}</td><td>{_e(e.inactive)}</td>"
            f"<td>{_e(e.inactive_reason or '')}</td>"
            "</tr>"
            for e in historic
        )
        historic_section = (
            '<h2 id="historic">No longer observed</h2>\n'
            "<p>These entities have not been observed on the network recently. Their pages are kept for reference.</p>\n"
            '<div class="table-wrap"><table><thead><tr><th>Entity</th><th>First seen</th><th>Last seen</th><th>Note</th></tr></thead>'
            f"<tbody>\n{historic_rows}\n</tbody></table></div>"
        )
    else:
        historic_section = ""

    content = _template("index.html").substitute(
        site_description=_e(SITE_DESCRIPTION),
        entity_cards=entity_cards,
        historic_section=historic_section,
        repo_url=_e(REPO_URL),
        json_file=OUTPUT_JSON,
        schema_file=OUTPUT_SCHEMA,
    )
    return render_page(SITE_TITLE, SITE_DESCRIPTION, SITE_URL, "./", content, brand_is_heading=True)


def render_entity(entity: Entity) -> str:
    root = "../"

    if entity.active:
        status = f'<span class="status active">observed</span> since {_e(entity.added)}'
    else:
        status = (
            f'<span class="status inactive">not observed since {_e(entity.inactive)}</span>, '
            f"first seen {_e(entity.added)}"
        )
        if entity.inactive_reason:
            status += f". {_e(entity.inactive_reason)}"

    sections = []
    if entity.user_agents:
        items = "".join(f"<li><code>{_e(ua)}</code></li>" for ua in entity.user_agents)
        sections.append(f"<h2>User agents</h2>\n<ul>{items}</ul>")
    # The references follow the description directly, without a heading.
    if entity.references:
        items = "".join(
            f'<li><a href="{_e(r.url)}">{_e(r.title)}</a>'
            + (f' <span class="muted">({_e(r.date)})</span>' if r.date else "")
            + "</li>"
            for r in entity.references
        )
        references_html = f'<ul class="references">{items}</ul>'
    else:
        references_html = ""

    if entity.active_networks:
        active_html = (
            f"<h2>Networks in use</h2>\n{_networks_table(entity.active_networks, historic=False)}"
        )
    elif entity.active:
        active_html = "<h2>Networks in use</h2><p>None known.</p>"
    else:
        active_html = "<h2>Networks in use</h2><p>None. This entity is no longer observed on the network.</p>"

    if entity.historic_networks:
        historic_html = (
            f"<h2>Previously used networks</h2>\n{_networks_table(entity.historic_networks, historic=True)}"
        )
    else:
        historic_html = ""

    # GitHub's edit view offers a fork and a pull request to anyone without
    # write access, which is the normal way to contribute a change here.
    edit_url = f"{REPO_URL}/edit/main/entities/{entity.slug}.toml"
    content = _template("entity.html").substitute(
        name=_e(entity.name),
        chips=_chips(entity.tags, root),
        status=status,
        summary=_e(entity.summary),
        description=format_text(entity.description),
        references=references_html,
        sections="\n".join(sections),
        active_networks=active_html,
        historic_networks=historic_html,
        edit_url=_e(edit_url),
    )
    return render_page(
        f"{entity.name} - {SITE_TITLE}", entity.summary, f"{SITE_URL}{entity.slug}/", root, content
    )


def render_tags(dataset: Dataset) -> str:
    root = "../"
    sections = []
    for tag, description in dataset.tags.items():
        carriers = [e for e in dataset.entities if tag in e.tags]
        if carriers:
            links = ", ".join(
                f'<a href="{root}{_e(e.slug)}/">{_e(e.name)}</a>' for e in carriers
            )
            entities_line = f'<p class="muted">{_plural(len(carriers), "entity", "entities")}: {links}</p>'
        else:
            entities_line = '<p class="muted">No entities carry this tag.</p>'
        sections.append(
            f'<h2 id="tag-{_e(tag)}"><span class="tag">{_e(tag)}</span></h2>\n'
            f"<p>{_e(description)}</p>\n{entities_line}"
        )
    content = _template("tags.html").substitute(
        tag_sections="\n\n".join(sections) or "<p>No tags defined.</p>",
    )
    return render_page(
        f"Tags - {SITE_TITLE}",
        "The vocabulary used to describe what the listed entities were seen doing.",
        f"{SITE_URL}{TAGS_DIR}/",
        root,
        content,
    )


def banlist_data(dataset: Dataset) -> dict:
    """The data the banlist generator needs: active entities and their networks."""
    return {
        "entities": [
            {
                "slug": e.slug,
                "name": e.name,
                "tags": e.tags,
                "networks": [n.text for n in e.active_networks],
            }
            for e in sorted(dataset.active_entities, key=lambda e: e.name.casefold())
        ]
    }


def render_banlist(dataset: Dataset) -> str:
    root = "../"
    data = banlist_data(dataset)
    entities = data["entities"]

    tag_filters = "\n".join(
        '<label class="toggle"><input type="checkbox" class="tag-toggle" value="{tag}">'
        '<span class="tag">{tag}</span> <span class="muted">{description}</span></label>'.format(
            tag=_e(t), description=_e(d)
        )
        for t, d in dataset.tags.items()
    )

    rows = []
    for e in entities:
        rows.append(
            '<tr><td><label class="toggle"><input type="checkbox" class="entity-toggle" '
            f'value="{_e(e["slug"])}" data-tags="{_e(" ".join(e["tags"]))}"> {_e(e["name"])}</label></td>'
            f'<td>{_chips(e["tags"], root)}</td>'
            f'<td class="num">{len(e["networks"])}</td>'
            f'<td><a href="{root}{_e(e["slug"])}/">details</a></td></tr>'
        )
    entity_rows = "\n".join(rows) or '<tr><td colspan="4">No entities to select.</td></tr>'

    # Embedded as JSON so the page works offline and without a fetch. "</" is
    # escaped so the blob can never close the surrounding script element.
    data_json = json.dumps(data, separators=(",", ":")).replace("</", "<\\/")
    content = _template("banlist.html").substitute(
        entity_rows=entity_rows,
        tag_filters=tag_filters or '<p class="muted">No tags defined.</p>',
        default_bantime_days=DEFAULT_BANTIME_DAYS,
        json_file=OUTPUT_JSON,
        root=root,
        data_json=data_json,
    )
    scripts = f'<script src="{root}banlist.js" defer></script>'
    return render_page(
        f"Banlist generator - {SITE_TITLE}",
        "Pick the entities you want your Bitcoin node to ban and generate the matching setban commands.",
        f"{SITE_URL}{BANLIST_DIR}/",
        root,
        content,
        scripts,
    )


def write_site(dataset: Dataset, outdir: Path) -> None:
    outdir.mkdir(parents=True, exist_ok=True)
    write_exports(dataset, outdir)
    shutil.copy(STATIC_DIR / "style.css", outdir / "style.css")
    shutil.copy(STATIC_DIR / "banlist.js", outdir / "banlist.js")
    shutil.copy(STATIC_DIR / "theme.js", outdir / "theme.js")
    (outdir / ".nojekyll").write_text("")
    (outdir / "index.html").write_text(render_index(dataset), encoding="utf-8")
    tags_page_dir = outdir / TAGS_DIR
    tags_page_dir.mkdir(exist_ok=True)
    (tags_page_dir / "index.html").write_text(render_tags(dataset), encoding="utf-8")
    banlist_page_dir = outdir / BANLIST_DIR
    banlist_page_dir.mkdir(exist_ok=True)
    (banlist_page_dir / "index.html").write_text(render_banlist(dataset), encoding="utf-8")
    for entity in dataset.entities:
        page_dir = outdir / entity.slug
        page_dir.mkdir(exist_ok=True)
        (page_dir / "index.html").write_text(render_entity(entity), encoding="utf-8")
    print(f"wrote index, tags, banlist generator and {len(dataset.entities)} entity pages to {outdir}")


# --------------------------------------------------------------------------
# CLI
# --------------------------------------------------------------------------


def run_self_tests() -> bool:
    """Runs the unit tests next to this script. Prints details only on failure."""
    import io
    import unittest

    # The tests import this file as the module "entities". When running as a
    # script we are "__main__", so register ourselves under that name to make
    # the tests exercise this very module instead of a second copy.
    sys.modules.setdefault("entities", sys.modules[__name__])
    suite = unittest.defaultTestLoader.discover(str(HERE), pattern="test_*.py")
    output = io.StringIO()
    result = unittest.TextTestRunner(stream=output, verbosity=1).run(suite)
    if not result.wasSuccessful():
        print(output.getvalue(), file=sys.stderr)
        print("error: self-tests failed, refusing to continue", file=sys.stderr)
        return False
    print(f"self-tests passed ({result.testsRun} tests)")
    return True


def main(argv: list[str] | None = None, *, self_test: bool = True) -> int:
    if self_test and not run_self_tests():
        return 1

    parser = argparse.ArgumentParser(
        prog="entities", description="Validate the dataset and generate the website."
    )
    parser.add_argument("--entities", type=Path, default=DEFAULT_ENTITIES_DIR, help="entities directory")
    parser.add_argument("--tags", type=Path, default=DEFAULT_TAGS_FILE, help="tags.toml file")
    sub = parser.add_subparsers(dest="command", required=True)

    sub.add_parser("validate", help="validate all entity files")
    site = sub.add_parser("site", help="write the website and the JSON export")
    site.add_argument("outdir", type=Path, help="output directory")

    args = parser.parse_args(argv)
    try:
        dataset = load_dataset(args.entities, args.tags)
    except ValidationError as e:
        for error in e.errors:
            print(f"error: {error}", file=sys.stderr)
        print(f"{len(e.errors)} error(s)", file=sys.stderr)
        return 1

    networks = sum(len(e.networks) for e in dataset.entities)
    active = sum(len(e.active_networks) for e in dataset.active_entities)
    print(f"loaded {len(dataset.entities)} entities with {networks} networks ({active} in use)")

    if args.command == "validate":
        return 0
    args.outdir.mkdir(parents=True, exist_ok=True)
    write_site(dataset, args.outdir)
    return 0


if __name__ == "__main__":
    sys.exit(main())
