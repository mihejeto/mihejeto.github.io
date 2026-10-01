# Michael Ihejeto

I build offers, funnels and content systems for independent professionals, and I'm paid on results.

**Live:** https://mihejeto.github.io

## What's here

- `index.html` — how I work with partners.
- `demo/dashboard.html` — a live example of the revenue-attribution dashboard every partner gets. Example data, real output.
- `demo/tracker.py` — the tool that generates it. Python 3, standard library only, no dependencies.

## The tracker

Every lead that enters a partner's funnel is issued a unique reference at form submission, carries that reference into the booking, and is tracked through five stages: `new → booked → showed → won → lost`.

Revenue is only counted above an agreed written baseline:

```
attributable = max(0, gross - baseline_monthly * months)
our_share    = attributable * split_pct / 100
```

So a partner never pays a share of income they were already earning before I started.

Run it:

```bash
python3 tracker.py demo      # build the example database and dashboard
python3 tracker.py report    # per-partner funnel and revenue summary
python3 tracker.py dashboard # regenerate dashboard.html
```

Contact: mikejeto@gmail.com
