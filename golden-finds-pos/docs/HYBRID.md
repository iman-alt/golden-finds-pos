# Offline and online

The requirement was that it works both offline and online. It does — but
which of the two is *in charge* is the decision that matters, and getting
it wrong is expensive.

## The rule

**The shop's computer holds the real data. Everything else is a window
onto it.**

That single sentence is what makes both halves possible without either
one breaking the other.

## Why not host it and be done

The obvious version — put it on a server, everyone visits a website — has
one fatal property for this shop: at 2pm on a Tuesday when the line is
down and a customer is standing at the counter with a basket, the shop
cannot sell anything. A till that stops trading when the internet does is
not a till.

## Why not run two copies and sync them

The other tempting version — a copy online, a copy in the shop, synced —
sounds like it gives you both. It does not, and the reason is worth
understanding before anyone tries it:

Mum, at home, changes the price of sugar to 160. At the same moment the
shop, offline, sells three bags at 150. When the line comes back, which
is right? Both are. Nothing in the data says which should win.

That problem has real solutions, and all of them cost more than this shop
will ever get back: every row needs a version and a timestamp, every
conflict needs a rule, and every rule needs someone to have thought about
what it means for money. The failure mode is not an error message — it is
a stock level that is quietly wrong, or a sale that disappears.

So: one source of truth, and it lives where the selling happens.

## What you actually get

### Offline — the shop, always

`python serve.py` runs the app on the shop computer, bound to the local
network. The till works with the router unplugged and the internet
account unpaid. This is the primary way the system is used and it never
depends on anything outside the building.

### On the shop wifi — already working

Because it serves to the local network rather than only to itself, any
phone or tablet on the shop wifi reaches it at the address `serve.py`
prints on startup. Useful for stock-taking down an aisle instead of
walking back to the counter. Still no internet involved.

### Online — the same computer, reachable from outside

To let Mum check takings from home, you expose the machine that already
has the data, rather than standing up a second copy of it. A tunnel does
this: the shop computer makes an outbound connection to a relay, and the
relay gives you a URL.

Recommended, in order:

1. **Tailscale** — a private network between her devices and the shop
   computer. Not public, nothing to secure beyond the login the app
   already has, free at this size. Best choice.
2. **Cloudflare Tunnel** — a real public HTTPS address. Use if she wants
   to reach it from a device you cannot install Tailscale on.

Either way:

- Nothing changes in this codebase. There is no "online mode".
- The shop keeps trading when the tunnel is down; only the remote view
  stops working.
- There is no second copy, so there is nothing to sync and nothing to
  conflict.

### Setting up a tunnel

With Tailscale installed on the shop computer and on her phone, and
`serve.py` running:

```bash
tailscale serve --bg 8000
```

The address it prints works from any of her devices, anywhere.

Before doing this, make sure of two things:

- `SESSION_COOKIE_SECURE=1` in the environment, since the tunnel is HTTPS.
- Every account has a PIN that is not a starting PIN someone else chose.
  A till on a local network only is protected by the door lock as well as
  the PIN. Once it is reachable from outside, the PIN is the only thing
  left.

## If the shop later needs true multi-site

If there is ever a second shop, the answer is not sync — it is to move
the database to one hosted server that both tills talk to, and to keep a
local read-only copy for outages. The data model here does not assume one
machine, so that move is a deployment change rather than a rewrite.

That is a decision for when there is a second shop. Not before.

## Summary

| | Where the data is | Works with no internet |
|---|---|---|
| Till, shop computer | on that computer | yes |
| Phone on shop wifi | on that computer | yes |
| Mum's phone, at home | on that computer | no — needs the line up |

One copy of the truth, in the shop, reachable from wherever it is needed.
