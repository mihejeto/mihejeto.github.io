#!/usr/bin/env python3
"""
PARTNER OS — revenue attribution tracker for shadow-operating partnerships.

WHY THIS EXISTS
---------------
We are paid a percentage of NEW revenue we generate for a partner.
We are remote, invisible, and have no access to their bank account.
So we never ask "how much did you make?" — we hold the record ourselves.

THE CHAIN OF CUSTODY
  partner's video  ->  OUR landing page  ->  OUR form (lead_id issued here)
  ->  OUR calendar (lead_id travels with the booking)  ->  call happens
  ->  outcome recorded  ->  revenue logged  ->  our share computed

Every lead is born inside our system. Nothing is reconstructed from memory.

USAGE
  python3 tracker.py init                      # create db + schema
  python3 tracker.py demo                      # load a worked example
  python3 tracker.py add-partner "Name" --split 25 --baseline 3000
  python3 tracker.py add-lead 1 --name "..." --email "..." --source "video title"
  python3 tracker.py set-stage <lead_id> booked|showed|won|lost
  python3 tracker.py log-payment <lead_id> 2997 --note "deposit"
  python3 tracker.py report                    # console summary
  python3 tracker.py dashboard                 # write dashboard.html
"""

import argparse
import datetime as dt
import os
import sqlite3
import secrets

DB = os.path.join(os.path.dirname(os.path.abspath(__file__)), "partner_os.db")

# Pipeline stages, in order. Anything past 'showed' is a real conversation.
STAGES = ["new", "booked", "showed", "won", "lost"]


def conn():
    c = sqlite3.connect(DB)
    c.row_factory = sqlite3.Row
    c.execute("PRAGMA foreign_keys = ON")
    return c


def now():
    return dt.datetime.now().isoformat(timespec="seconds")


# ----------------------------------------------------------------------------
# schema
# ----------------------------------------------------------------------------

SCHEMA = """
CREATE TABLE IF NOT EXISTS partners (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    name         TEXT NOT NULL,
    niche        TEXT,
    -- our percentage of NEW revenue, agreed in writing before day one
    split_pct    REAL NOT NULL DEFAULT 25.0,
    -- their average monthly revenue BEFORE we started. we are never paid on this.
    baseline_mrr REAL NOT NULL DEFAULT 0.0,
    -- how they take money. 'ours' = our Stripe, we see every payment ourselves.
    checkout     TEXT NOT NULL DEFAULT 'theirs',
    started_on   TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS leads (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    partner_id  INTEGER NOT NULL REFERENCES partners(id),
    -- the unique token issued at form submission. travels into the booking.
    ref         TEXT NOT NULL UNIQUE,
    name        TEXT,
    email       TEXT,
    -- which video / post / page the lead came from. this is the whole point.
    source      TEXT,
    stage       TEXT NOT NULL DEFAULT 'new',
    created_at  TEXT NOT NULL,
    booked_at   TEXT,
    closed_at   TEXT
);

CREATE TABLE IF NOT EXISTS payments (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    lead_id     INTEGER NOT NULL REFERENCES leads(id),
    amount      REAL NOT NULL,
    paid_at     TEXT NOT NULL,
    note        TEXT,
    -- 'ours'  = seen directly in our checkout. undeniable.
    -- 'theirs'= reported by the partner. reconcile monthly against the calendar.
    evidence    TEXT NOT NULL DEFAULT 'theirs'
);
"""


def cmd_init(_):
    with conn() as c:
        c.executescript(SCHEMA)
    print(f"initialised {DB}")


# ----------------------------------------------------------------------------
# writes
# ----------------------------------------------------------------------------

def cmd_add_partner(a):
    with conn() as c:
        cur = c.execute(
            "INSERT INTO partners (name,niche,split_pct,baseline_mrr,checkout,started_on)"
            " VALUES (?,?,?,?,?,?)",
            (a.name, a.niche, a.split, a.baseline, a.checkout, now()),
        )
        print(f"partner {cur.lastrowid}: {a.name} | our share {a.split}% of revenue above ${a.baseline:,.0f}/mo")


def cmd_add_lead(a):
    ref = "L-" + secrets.token_hex(3).upper()
    with conn() as c:
        cur = c.execute(
            "INSERT INTO leads (partner_id,ref,name,email,source,stage,created_at)"
            " VALUES (?,?,?,?,?,'new',?)",
            (a.partner_id, ref, a.name, a.email, a.source, now()),
        )
        print(f"lead {cur.lastrowid} ref={ref} source={a.source!r}")


def cmd_set_stage(a):
    if a.stage not in STAGES:
        raise SystemExit(f"stage must be one of {STAGES}")
    field = {"booked": "booked_at", "won": "closed_at", "lost": "closed_at"}.get(a.stage)
    with conn() as c:
        c.execute("UPDATE leads SET stage=? WHERE id=?", (a.stage, a.lead_id))
        if field:
            c.execute(f"UPDATE leads SET {field}=? WHERE id=?", (now(), a.lead_id))
    print(f"lead {a.lead_id} -> {a.stage}")


def cmd_log_payment(a):
    with conn() as c:
        c.execute(
            "INSERT INTO payments (lead_id,amount,paid_at,note,evidence) VALUES (?,?,?,?,?)",
            (a.lead_id, a.amount, now(), a.note, a.evidence),
        )
        c.execute("UPDATE leads SET stage='won', closed_at=? WHERE id=?", (now(), a.lead_id))
    print(f"logged ${a.amount:,.2f} against lead {a.lead_id} [{a.evidence}]")


# ----------------------------------------------------------------------------
# maths
# ----------------------------------------------------------------------------

def partner_rows():
    """Everything the dashboard and the report need, per partner."""
    out = []
    with conn() as c:
        for p in c.execute("SELECT * FROM partners ORDER BY id"):
            leads = c.execute(
                "SELECT * FROM leads WHERE partner_id=? ORDER BY id DESC", (p["id"],)
            ).fetchall()
            pays = c.execute(
                "SELECT pay.*, l.ref, l.source, l.name AS lead_name FROM payments pay"
                " JOIN leads l ON l.id = pay.lead_id WHERE l.partner_id=?"
                " ORDER BY pay.id DESC",
                (p["id"],),
            ).fetchall()

            gross = sum(x["amount"] for x in pays)
            verified = sum(x["amount"] for x in pays if x["evidence"] == "ours")

            months = max(1, len({x["paid_at"][:7] for x in pays})) if pays else 1
            # We are only paid on revenue ABOVE the agreed baseline.
            attributable = max(0.0, gross - p["baseline_mrr"] * months)
            our_share = attributable * p["split_pct"] / 100.0

            counts = {s: sum(1 for l in leads if l["stage"] == s) for s in STAGES}
            booked_plus = counts["booked"] + counts["showed"] + counts["won"] + counts["lost"]

            out.append(
                {
                    "p": p,
                    "leads": leads,
                    "pays": pays,
                    "gross": gross,
                    "verified": verified,
                    "months": months,
                    "attributable": attributable,
                    "our_share": our_share,
                    "counts": counts,
                    "booked_plus": booked_plus,
                    "book_rate": (booked_plus / len(leads) * 100) if leads else 0.0,
                    "close_rate": (counts["won"] / booked_plus * 100) if booked_plus else 0.0,
                }
            )
    return out


def cmd_report(_):
    rows = partner_rows()
    if not rows:
        print("no partners yet — run: python3 tracker.py demo")
        return
    total = 0.0
    for r in rows:
        p = r["p"]
        print(f"\n{'='*64}\n{p['name']}  ({p['niche'] or 'n/a'})")
        print(f"  split {p['split_pct']:.0f}%   baseline ${p['baseline_mrr']:,.0f}/mo   checkout: {p['checkout']}")
        print(f"  leads {len(r['leads'])}  ->  booked {r['booked_plus']} ({r['book_rate']:.0f}%)  ->  won {r['counts']['won']} ({r['close_rate']:.0f}% of calls)")
        print(f"  gross generated      ${r['gross']:,.2f}")
        print(f"  of which verified    ${r['verified']:,.2f}  (seen in our own checkout)")
        print(f"  less baseline        ${p['baseline_mrr']*r['months']:,.2f}  ({r['months']} mo)")
        print(f"  attributable         ${r['attributable']:,.2f}")
        print(f"  >> OUR SHARE         ${r['our_share']:,.2f}")
        total += r["our_share"]
    print(f"\n{'='*64}\nTOTAL OWED TO US: ${total:,.2f}\n")


# ----------------------------------------------------------------------------
# dashboard  (self-contained, inline styles — renders in a sandboxed iframe)
# ----------------------------------------------------------------------------

def cmd_dashboard(_):
    rows = partner_rows()
    total = sum(r["our_share"] for r in rows)
    gross_all = sum(r["gross"] for r in rows)
    verified_all = sum(r["verified"] for r in rows)

    CARD = "background:#0e131a;border:1px solid #1e2733;border-radius:11px;padding:20px 22px;"
    MUTED = "color:#8a98a8;font-size:12px;letter-spacing:.06em;text-transform:uppercase;margin:0 0 6px;"
    BIG = "font-size:30px;font-weight:700;color:#eef3f8;margin:0;"

    parts = [f"""<!doctype html><html><head><meta charset="utf-8">
<title>Partner OS</title></head>
<body style="margin:0;background:#070a0e;font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',Roboto,Helvetica,Arial,sans-serif;color:#eef3f8;">
<div style="max-width:1080px;margin:0 auto;padding:28px 20px 60px;">

<div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:18px;padding-bottom:14px;border-bottom:1px solid #171f29;"><span style="font-size:12px;letter-spacing:.18em;text-transform:uppercase;color:#d8b26a;font-weight:600;">Partner OS &middot; revenue attribution</span><a href="../index.html" style="font-size:13.5px;color:#8a98a8;text-decoration:none;">&larr; Back to michaelihejeto</a></div>
<h1 style="margin:0 0 4px;font-size:27px;">What our work earned, and what we are owed</h1>
<p style="margin:0 0 24px;color:#8a98a8;font-size:14px;">
Every lead below entered through a page we control and carries a reference issued at form submission.
Nothing here is reconstructed from anyone's memory. Generated {dt.datetime.now():%d %b %Y, %H:%M}.</p>

<div style="display:flex;gap:14px;flex-wrap:wrap;margin-bottom:26px;">
  <div style="{CARD}flex:1;min-width:200px;"><p style="{MUTED}">Revenue generated</p><p style="{BIG}">${gross_all:,.0f}</p></div>
  <div style="{CARD}flex:1;min-width:200px;"><p style="{MUTED}">Verified in our checkout</p><p style="{BIG}">${verified_all:,.0f}</p></div>
  <div style="{CARD}flex:1;min-width:200px;border-color:#8a7342;background:#121922;"><p style="{MUTED}">Our share, owed</p><p style="{BIG}color:#d8b26a;">${total:,.0f}</p></div>
</div>
"""]

    for r in rows:
        p = r["p"]
        chk_ok = p["checkout"] == "ours"
        chip_bg, chip_fg, chip_txt = (
            ("#12211a", "#4ec98a", "our checkout &middot; payments self-verifying")
            if chk_ok
            else ("#221c12", "#c79a4e", "their checkout &middot; reconcile monthly")
        )

        parts.append(f"""
<div style="{CARD}margin-bottom:22px;">
  <div style="display:flex;justify-content:space-between;align-items:baseline;flex-wrap:wrap;gap:8px;">
    <h2 style="margin:0;font-size:20px;">{p['name']}</h2>
    <span style="font-size:12px;color:#8a98a8;">{p['niche'] or ''} &middot; {p['split_pct']:.0f}% of new revenue &middot; baseline ${p['baseline_mrr']:,.0f}/mo</span>
  </div>
  <div style="display:inline-block;margin-top:10px;padding:4px 10px;border-radius:999px;font-size:12px;background:{chip_bg};color:{chip_fg};">{chip_txt}</div>

  <table style="width:100%;border-collapse:collapse;margin-top:16px;font-size:13px;">
    <tr>
      <td style="padding:8px 0;color:#8a98a8;">Leads captured</td><td style="text-align:right;font-weight:600;">{len(r['leads'])}</td>
      <td style="padding:8px 0 8px 26px;color:#8a98a8;">Calls booked</td><td style="text-align:right;font-weight:600;">{r['booked_plus']} ({r['book_rate']:.0f}%)</td>
      <td style="padding:8px 0 8px 26px;color:#8a98a8;">Closed</td><td style="text-align:right;font-weight:600;">{r['counts']['won']} ({r['close_rate']:.0f}% of calls)</td>
    </tr>
  </table>

  <table style="width:100%;border-collapse:collapse;margin-top:12px;font-size:13px;border-top:1px solid #1a222c;">
    <tr><td style="padding:7px 0;color:#8a98a8;">Gross revenue tracked to our funnel</td><td style="text-align:right;">${r['gross']:,.2f}</td></tr>
    <tr><td style="padding:7px 0;color:#8a98a8;">Less agreed baseline ({r['months']} mo &times; ${p['baseline_mrr']:,.0f})</td><td style="text-align:right;color:#c79a4e;">&minus;${p['baseline_mrr']*r['months']:,.2f}</td></tr>
    <tr><td style="padding:7px 0;font-weight:600;border-top:1px solid #1a222c;">New revenue we created</td><td style="text-align:right;font-weight:600;border-top:1px solid #1a222c;">${r['attributable']:,.2f}</td></tr>
    <tr><td style="padding:7px 0;font-weight:700;color:#d8b26a;">Our {p['split_pct']:.0f}%</td><td style="text-align:right;font-weight:700;color:#d8b26a;">${r['our_share']:,.2f}</td></tr>
  </table>
""")

        if r["leads"]:
            parts.append("""
  <p style="margin:20px 0 6px;font-size:12px;letter-spacing:.06em;text-transform:uppercase;color:#8a98a8;">Lead ledger</p>
  <table style="width:100%;border-collapse:collapse;font-size:12.5px;">
    <tr style="text-align:left;color:#8a98a8;border-bottom:1px solid #1e2733;">
      <th style="padding:7px 6px 7px 0;font-weight:500;">Ref</th>
      <th style="padding:7px 6px;font-weight:500;">Lead</th>
      <th style="padding:7px 6px;font-weight:500;">Came from</th>
      <th style="padding:7px 6px;font-weight:500;">Stage</th>
      <th style="padding:7px 0 7px 6px;font-weight:500;text-align:right;">Paid</th>
    </tr>""")
            pay_by_lead = {}
            for x in r["pays"]:
                pay_by_lead[x["lead_id"]] = pay_by_lead.get(x["lead_id"], 0) + x["amount"]
            for l in r["leads"]:
                amt = pay_by_lead.get(l["id"], 0)
                colour = {"won": "#4ec98a", "lost": "#5f6c7a", "showed": "#c79a4e"}.get(l["stage"], "#eef3f8")
                parts.append(f"""
    <tr style="border-bottom:1px solid #161d26;">
      <td style="padding:7px 6px 7px 0;font-family:ui-monospace,Menlo,monospace;color:#8a98a8;">{l['ref']}</td>
      <td style="padding:7px 6px;">{l['name'] or '—'}</td>
      <td style="padding:7px 6px;color:#8a98a8;">{l['source'] or '—'}</td>
      <td style="padding:7px 6px;color:{colour};font-weight:600;">{l['stage']}</td>
      <td style="padding:7px 0 7px 6px;text-align:right;">{('$'+format(amt,',.0f')) if amt else '—'}</td>
    </tr>""")
            parts.append("</table>")
        parts.append("</div>")

    parts.append("""
<div style="background:#0e131a;border:1px solid #1e2733;border-left:2px solid #d8b26a;color:#9aa8b8;border-radius:10px;padding:20px 22px;font-size:13px;line-height:1.65;">
<strong style="color:#eef3f8;">Why this holds up.</strong>
The partner's audience is sent to a page we own. The reference is issued by our form, not theirs, and travels into the
calendar booking — so a lead cannot later be reclassified as "a referral". Where the checkout is ours, payment is
observed directly and needs no one's word. Where it is theirs, the lead ledger and the calendar are reconciled monthly,
and any lead that entered through our funnel and bought within 90 days counts. The baseline is agreed in writing before
day one, so we are never paid on business they already had — and never argued out of business we created.
</div>

</div></body></html>""")

    out = os.path.join(os.path.dirname(DB), "dashboard.html")
    with open(out, "w") as f:
        f.write("".join(parts))
    print(f"wrote {out}")


# ----------------------------------------------------------------------------
# demo — a worked example, numbers deliberately conservative
# ----------------------------------------------------------------------------

def cmd_demo(_):
    cmd_init(None)
    with conn() as c:
        c.execute("DELETE FROM payments"); c.execute("DELETE FROM leads"); c.execute("DELETE FROM partners")
        c.execute(
            "INSERT INTO partners (id,name,niche,split_pct,baseline_mrr,checkout,started_on)"
            " VALUES (1,'EXAMPLE — aging life care manager','Care management',25,0,'ours',?)",
            (now(),),
        )
        seed = [
            ("Margaret R.",  "Siblings video",      "won",    1200),
            ("David K.",     "Siblings video",      "won",    1200),
            ("Alison P.",    "Phone-call video",    "won",     850),
            ("Tom H.",       "Phone-call video",    "showed",    0),
            ("Jean W.",      "Siblings video",      "lost",      0),
            ("Priya N.",     "Paid workshop page",  "won",      97),
            ("Carl B.",      "Paid workshop page",  "won",      97),
            ("Renee S.",     "Paid workshop page",  "won",      97),
            ("Hugh T.",      "Phone-call video",    "booked",    0),
            ("Nina F.",      "Siblings video",      "new",       0),
        ]
        for name, src, stage, amt in seed:
            ref = "L-" + secrets.token_hex(3).upper()
            cur = c.execute(
                "INSERT INTO leads (partner_id,ref,name,email,source,stage,created_at,booked_at,closed_at)"
                " VALUES (1,?,?,?,?,?,?,?,?)",
                (ref, name, name.split()[0].lower() + "@example.com", src, stage, now(),
                 now() if stage != "new" else None,
                 now() if stage in ("won", "lost") else None),
            )
            if amt:
                c.execute(
                    "INSERT INTO payments (lead_id,amount,paid_at,note,evidence) VALUES (?,?,?,?,'ours')",
                    (cur.lastrowid, amt, now(), "demo"),
                )
    print("demo data loaded")
    cmd_report(None)
    cmd_dashboard(None)


def main():
    ap = argparse.ArgumentParser(description="Partner OS — revenue attribution tracker")
    sub = ap.add_subparsers(dest="cmd", required=True)

    sub.add_parser("init").set_defaults(fn=cmd_init)
    sub.add_parser("demo").set_defaults(fn=cmd_demo)
    sub.add_parser("report").set_defaults(fn=cmd_report)
    sub.add_parser("dashboard").set_defaults(fn=cmd_dashboard)

    p = sub.add_parser("add-partner"); p.add_argument("name")
    p.add_argument("--niche", default=""); p.add_argument("--split", type=float, default=25.0)
    p.add_argument("--baseline", type=float, default=0.0)
    p.add_argument("--checkout", choices=["ours", "theirs"], default="theirs")
    p.set_defaults(fn=cmd_add_partner)

    p = sub.add_parser("add-lead"); p.add_argument("partner_id", type=int)
    p.add_argument("--name", default=""); p.add_argument("--email", default="")
    p.add_argument("--source", default=""); p.set_defaults(fn=cmd_add_lead)

    p = sub.add_parser("set-stage"); p.add_argument("lead_id", type=int)
    p.add_argument("stage"); p.set_defaults(fn=cmd_set_stage)

    p = sub.add_parser("log-payment"); p.add_argument("lead_id", type=int)
    p.add_argument("amount", type=float); p.add_argument("--note", default="")
    p.add_argument("--evidence", choices=["ours", "theirs"], default="theirs")
    p.set_defaults(fn=cmd_log_payment)

    a = ap.parse_args()
    a.fn(a)


if __name__ == "__main__":
    main()
