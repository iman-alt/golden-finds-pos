# Golden Finds POS

A point-of-sale and stock system for a small retail shop, built to keep
working when the internet does not.

It runs as a web app on the shop's own computer. The browser is just the
interface — nothing leaves the machine, and the till keeps trading through
a power cut or an outage. Because it is served over the local network, a
phone or tablet on the shop wifi can reach it too, which is useful for
stock-taking away from the counter.

Money is never stored as a floating-point number, prices are never
decided by the browser, and every sale, stock movement and price change
carries the name of the person who made it.

## Running it

```bash
python -m venv .venv
.venv/Scripts/pip install -r requirements.txt
python serve.py
```

Open the address it prints. The first screen asks you to create the owner
account; after that the setup page seals itself.

For development, `python run.py` runs Flask's reloader on port 5000
instead.

**Set up backups before the shop starts using it** — see
[docs/BACKUP.md](golden-finds-pos/docs/BACKUP.md).

## How it works

### Everything is in one SQLite file

One shop, one machine, no database server to install or keep running. A
backup is a copy of one file, and moving to a new computer is moving that
file. `stock_movements` is an append-only ledger; the stock levels
elsewhere are caches of it and can be rebuilt from it at any time.

### Prices are decided by the server

The browser holds product ids and quantities. It asks the server what the
cart costs, and it renders the answer. It cannot send a price.

Both the cart preview and the recorded sale go through the same function,
so what the customer is quoted is what the shop books. An earlier version
priced the cart in JavaScript and ended up showing an offer price while
recording full retail.

### Pricing order

1. An active, owner-approved offer.
2. Wholesale, once quantity reaches that product's threshold.
3. Retail.

### Expiry and offers

Products marked as expiring are received in batches and always sold
soonest-expiry-first. The dashboard shows what is approaching its date,
tiered by urgency, with the value still sitting on the shelf.

The system never discounts anything by itself. It says which stock is
becoming a problem and suggests prices; a person picks one and approves
it, and the offer records who approved it. Offers can be given an end
date, after which they stop applying on their own.

### Two roles

**Cashier** — the till, stock in, their own sales.
**Owner** — everything, plus takings, margins, offer prices, voids, stock
adjustments, staff, and the activity log.

Sign-in is a name and a PIN, because a cashier at a counter is not going
to type a password between customers. PINs are hashed with scrypt and
rate-limited, and accounts lock after repeated wrong attempts.

### What is written down

Voided sales are kept and marked, never deleted — a till that can make
transactions disappear is a till that can be stolen from. Returns refund
the price actually paid, not today's price, and put saleable goods back
in the batch they came from. Price changes, voids, PIN resets and failed
sign-ins all land in the activity log.

## Commands

```bash
flask --app run backup          # verified snapshot, prunes old ones
flask --app run list-backups    # what exists, and how old
flask --app run restore <file>  # replace the live database
flask --app run check-stock     # report any drift from the ledger
flask --app run create-admin    # add an owner account
```

## Tests

```bash
.venv/Scripts/python -m pytest
```

125 tests, covering pricing and offers, FEFO batch consumption,
authentication and access control, the sale lifecycle, backup and
restore, and that every page renders for both roles.

## Layout

```
app/
  money.py          integer cents; parsing and formatting
  db.py             connections, transactions, audit log
  security.py       PINs, sessions, roles, CSRF
  backup.py         snapshot, verify, restore
  schema.sql        every table, money as INTEGER cents
  services/         business logic, no HTTP
  views/            routes, no business logic
templates/  static/  tests/  docs/
```

## Where it can go

The data model does not assume one machine. If the shop later wants the
system reachable from outside, the same code runs against a hosted
database with the local copy kept as the fallback. That is a later
decision, and nothing here forecloses it.
