# network-entities

Unusual, misbehaving, and possibly malicious entities on the Bitcoin
peer-to-peer network, including some that appear to spy on or track its users:
who they are, which addresses they use, and what they have been observed doing.

The website is at https://bitcoin-noc.github.io/network-entities/.

Every entry rests on observed behaviour and links to the evidence. The
[banlist generator](https://bitcoin-noc.github.io/network-entities/banlist/)
turns a selection you make yourself into `setban` commands for Bitcoin Core; the
repository ships no ready-made banlist.

The collection is centralized, maintained by hand, and certainly incomplete.

## Using the data

- https://bitcoin-noc.github.io/network-entities/entities.json contains all
  entities with their networks and metadata, described by
  https://bitcoin-noc.github.io/network-entities/schema.json.
- The `entities/*.toml` files in this repository are the source of truth.

## Adding an entity

An entity belongs here when it misbehaves, behaves strangely, or looks like it
is watching the network rather than taking part in it: connecting to every node
at once, listening for transaction announcements, using fake user agents, or
moving to fresh address ranges whenever the old ones become known. It may
connect out to other nodes, or be a reachable node that others connect to. The
behaviour has to have been observed and has to be describable.

Describe what was observed and link to the evidence. Draw the conclusion the
evidence supports, and leave it visible as a conclusion: "listens for
transaction announcements, which likely lets it link transactions to IP
addresses" is useful, "spies on users" on its own is not. If you are unsure
whether something is worth listing, open an issue first.

Add a file `entities/<slug>.toml`, where `<slug>` is a short lowercase
identifier (`^[a-z0-9][a-z0-9-]*$`) that becomes the URL of the entity's page.
Then run `python contrib/entities.py validate` (Python 3.11 or newer, no
dependencies).

```toml
name = "Unique name"                   # required. Someone searching for the
                                       # entity should be able to find this name.
summary = "One sentence about the entity and what it does."  # required
added = 2009-01-03                     # required, when the entity was first seen
tags = ["tx-spy"]                      # required, may be empty; see tags.toml
inactive = 2009-06-01                  # optional, when it was last observed
inactive_reason = "No longer connecting."  # optional, only with inactive
user_agents = ["/Satoshi:0.1.0/"]      # optional

description = """
Plain text, required. Blank lines separate paragraphs. Lines starting with
"- " form a bullet list, `backticks` mark code, and bare https:// URLs become
links. No HTML or Markdown.
"""

[[references]]                         # optional, repeat as needed
url = "https://example.com/post"
title = "Title of the linked page"
date = 2009-01-03                      # optional

[[networks]]                           # repeat for each address or range
cidr = "208.67.222.0/24"               # IPv4/IPv6 address or CIDR; host bits must be zero
added = 2009-01-03                     # optional, defaults to the entity's added date
note = "Observed connecting daily."    # optional

[[networks]]
cidr = "208.67.222.222"                # a single host
removed = 2009-02-01                   # optional, when the entity stopped using
                                       # this address; it stays as history
```

For entities with hundreds of single addresses, put them into
`entities/<slug>.ips.txt` instead, one address or network per line (`#` starts
a comment). They are treated as addresses in use since the entity's `added`
date.

Prefer listing a whole range only when the whole range is used by the entity.
A range that also contains unrelated hosts makes the data less useful and, for
anyone acting on it, catches innocent peers. When in doubt, list the individual
addresses.

Tags describe observed behaviour and must be defined in `tags.toml`. Reuse
existing tags where possible; add a new one with a one-line description if none
fits.

When an entity changes its addresses, keep the old networks and set `removed`
on them instead of deleting them. When an entity stops showing up on the
network, set `inactive` and `inactive_reason`; its page is kept for reference.

## Generating the website

```
python contrib/entities.py validate
python contrib/entities.py site OUTDIR
```

See `contrib/README.md` for details.
