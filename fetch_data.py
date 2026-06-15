#!/usr/bin/env python3
"""
Modern Menopause — Daily Data Fetcher
GitHub Action runs at 9:15am ET → writes data.json → commits to repo.
Claude Artifact reads raw GitHub URL to render dashboard.
"""

import json, os, sys, urllib.request, urllib.parse, urllib.error
from datetime import date, timedelta

today = date.today()
yest  = today - timedelta(days=1)
ago7  = today - timedelta(days=7)
ago14 = today - timedelta(days=14)
ago30 = today - timedelta(days=30)

YEST  = yest.isoformat()
AGO7  = ago7.isoformat()
AGO14 = ago14.isoformat()
AGO30 = ago30.isoformat()

WINDSOR_API_KEY = os.environ["WINDSOR_API_KEY"]
POSTHOG_API_KEY = os.environ["POSTHOG_API_KEY"]
POSTHOG_PROJECT = os.environ["POSTHOG_PROJECT_ID"]
GA_ACCOUNT      = "230-778-8239"
PURCHASE_CONV   = "[gtag] purchase"

def safe_float(v):
    try: return float(v or 0)
    except: return 0.0

# ── WINDSOR ──────────────────────────────────────────────────────────────────
def windsor_get(fields, date_from, date_to):
    params = urllib.parse.urlencode({
        "api_key":    WINDSOR_API_KEY,
        "connector":  "google_ads",
        "account_id": GA_ACCOUNT,
        "date_from":  date_from,
        "date_to":    date_to,
        "fields":     ",".join(fields),
    })
    url = f"https://connectors.windsor.ai/google_ads?{params}"
    print(f"  → Windsor [{date_from}→{date_to}] fields={fields}", flush=True)
    req = urllib.request.Request(url, headers={"Accept": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=60) as r:
            body = r.read()
        print(f"    HTTP 200, {len(body)} bytes", flush=True)
    except urllib.error.HTTPError as e:
        body = e.read()
        print(f"    HTTP {e.code}: {body[:500]}", flush=True)
        raise
    data = json.loads(body)
    rows = data.get("data", data) if isinstance(data, dict) else data
    print(f"    {len(rows)} rows", flush=True)
    return rows

# ── POSTHOG ──────────────────────────────────────────────────────────────────
def posthog_sql(label, query):
    url = f"https://app.posthog.com/api/projects/{POSTHOG_PROJECT}/query/"
    payload = json.dumps({"query": {"kind": "HogQLQuery", "query": query}}).encode()
    req = urllib.request.Request(url, data=payload, headers={
        "Content-Type":  "application/json",
        "Authorization": f"Bearer {POSTHOG_API_KEY}",
    })
    print(f"  → PostHog: {label}", flush=True)
    try:
        with urllib.request.urlopen(req, timeout=90) as r:
            data = json.loads(r.read())
    except urllib.error.HTTPError as e:
        body = e.read()
        print(f"    HTTP {e.code}: {body[:500]}", flush=True)
        raise
    cols = data.get("columns", [])
    rows = [dict(zip(cols, row)) for row in data.get("results", [])]
    print(f"    {len(rows)} rows", flush=True)
    return rows

# ════════════════════════════════════════════════════════════════════════════
# STEP 1: Windsor — campaign metrics yesterday
# ════════════════════════════════════════════════════════════════════════════
print("\n=== Windsor: Campaign metrics (yesterday) ===", flush=True)
raw_camp = windsor_get(
    ["campaign", "campaign_type", "impressions", "clicks", "cost",
     "search_top_impression_share", "roas"],
    YEST, YEST
)

print("\n=== Windsor: Campaign conversions (yesterday) ===", flush=True)
raw_conv = windsor_get(
    ["campaign", "conversion_action_name", "conversions"],
    YEST, YEST
)

print("\n=== Windsor: Daily metrics 7d ===", flush=True)
raw_daily7 = windsor_get(
    ["date", "impressions", "clicks", "cost", "roas"],
    AGO7, YEST
)

print("\n=== Windsor: Daily conversions 7d ===", flush=True)
raw_daily7_conv = windsor_get(
    ["date", "conversion_action_name", "conversions"],
    AGO7, YEST
)

print("\n=== Windsor: 14d aggregate ===", flush=True)
raw_14      = windsor_get(["impressions","clicks","cost","roas"], AGO14, YEST)
raw_14_conv = windsor_get(["conversion_action_name","conversions"], AGO14, YEST)

print("\n=== Windsor: 30d aggregate ===", flush=True)
raw_30      = windsor_get(["impressions","clicks","cost","roas"], AGO30, YEST)
raw_30_conv = windsor_get(["conversion_action_name","conversions"], AGO30, YEST)

# ════════════════════════════════════════════════════════════════════════════
# STEP 2: Parse Windsor data
# ════════════════════════════════════════════════════════════════════════════
print("\n=== Parsing Windsor data ===", flush=True)

# Campaigns
camp_map = {}
for r in raw_camp:
    name = r.get("campaign") or r.get("campaign_name","")
    if not name: continue
    ctype = str(r.get("campaign_type","")).upper()
    camp_map[name] = {
        "campaign": name,
        "type": "PMAX" if "PERFORMANCE_MAX" in ctype or "pmax" in name.lower() else "SEARCH",
        "impr":   safe_float(r.get("impressions")),
        "clicks": safe_float(r.get("clicks")),
        "cost":   safe_float(r.get("cost")),
        "top_is": safe_float(r.get("search_top_impression_share")),
        "roas":   safe_float(r.get("roas")) / 100.0,   # Windsor returns × 100
        "purch": 0.0,
    }
for r in raw_conv:
    name = r.get("campaign") or r.get("campaign_name","")
    if r.get("conversion_action_name","") == PURCHASE_CONV and name in camp_map:
        camp_map[name]["purch"] += safe_float(r.get("conversions"))
campaigns = sorted(camp_map.values(), key=lambda x: -x["cost"])
print(f"  {len(campaigns)} campaigns parsed", flush=True)

# Daily 7d
daily_map = {}
for r in raw_daily7:
    d = str(r.get("date",""))[:10]
    if not d: continue
    daily_map[d] = {
        "date": d, "impr": safe_float(r.get("impressions")),
        "clicks": safe_float(r.get("clicks")), "cost": safe_float(r.get("cost")),
        "roas": safe_float(r.get("roas")) / 100.0,
        "purch": 0.0, "appts": 0, "forms": 0, "ph_purch": 0,
    }
for r in raw_daily7_conv:
    d = str(r.get("date",""))[:10]
    if r.get("conversion_action_name","") == PURCHASE_CONV and d in daily_map:
        daily_map[d]["purch"] += safe_float(r.get("conversions"))
daily7 = sorted(daily_map.values(), key=lambda x: x["date"])
print(f"  {len(daily7)} daily rows parsed", flush=True)

def agg_conv(rows):
    return sum(safe_float(r.get("conversions")) for r in rows
               if r.get("conversion_action_name","") == PURCHASE_CONV)

def agg_metrics(rows):
    impr=clicks=cost=rn=rsum=0.0
    for r in rows:
        impr   += safe_float(r.get("impressions"))
        clicks += safe_float(r.get("clicks"))
        cost   += safe_float(r.get("cost"))
        rv = safe_float(r.get("roas",0))
        if rv: rsum += rv; rn += 1
    return {"impr":impr,"clicks":clicks,"cost":cost,"roas":(rsum/rn/100.0) if rn else 0.0}

agg14 = {**agg_metrics(raw_14),  "purch": agg_conv(raw_14_conv)}
agg30 = {**agg_metrics(raw_30),  "purch": agg_conv(raw_30_conv)}

# ════════════════════════════════════════════════════════════════════════════
# STEP 3: PostHog
# ════════════════════════════════════════════════════════════════════════════

print("\n=== PostHog: Daily funnel 7d (Google Paid) ===", flush=True)
ph_daily = posthog_sql("daily-funnel-7d", f"""
SELECT toDate(timestamp) AS day, event, count() AS cnt
FROM events
WHERE timestamp >= toDate('{AGO7}')
  AND timestamp < toDate('{YEST}') + INTERVAL 1 DAY
  AND properties.utm_source = 'google_paid'
  AND event IN ('booking_initial_assessment_clicked',
                'booking_form_submitted',
                'booking_payment_completed')
GROUP BY day, event
ORDER BY day
""")
ph_map = {}
for r in ph_daily:
    d = str(r.get("day",""))[:10]; e = r.get("event",""); cnt = int(r.get("cnt",0))
    if d not in ph_map: ph_map[d] = {"appts":0,"forms":0,"ph_purch":0}
    if   e == "booking_initial_assessment_clicked": ph_map[d]["appts"]    += cnt
    elif e == "booking_form_submitted":             ph_map[d]["forms"]     += cnt
    elif e == "booking_payment_completed":          ph_map[d]["ph_purch"]  += cnt
for d in daily7:
    if d["date"] in ph_map: d.update(ph_map[d["date"]])

print("\n=== PostHog: Channel funnel 30d ===", flush=True)
channels = posthog_sql("channels-30d", f"""
WITH sessions AS (
  SELECT
    properties.$session_id AS sid,
    multiIf(
      properties.utm_source = 'google_paid', 'Google Paid',
      properties.utm_source IN ('fb','facebook'), 'Meta Paid',
      properties.utm_medium IN ('Facebook_Mobile_Feed','Facebook_Desktop_Feed'), 'Meta Paid',
      properties.utm_source = 'ig', 'Meta Paid',
      properties.utm_source = 'paid_social_media', 'Meta Paid',
      properties.utm_source = 'bing', 'Bing Paid',
      properties.utm_source = 'chatgpt.com', 'ChatGPT',
      properties.utm_source IS NOT NULL AND properties.utm_source != '', 'Other Paid',
      properties.$referring_domain = '$direct' OR properties.$referring_domain IS NULL, 'Direct',
      properties.$referring_domain LIKE '%google%', 'Organic Search',
      properties.$referring_domain LIKE '%bing%', 'Organic Search',
      properties.$referring_domain LIKE '%facebook%' OR properties.$referring_domain LIKE '%instagram%', 'Organic Social',
      'Referral'
    ) AS channel
  FROM events
  WHERE event = '$pageview'
    AND timestamp >= toDate('{AGO30}')
    AND timestamp < toDate('{YEST}') + INTERVAL 1 DAY
  GROUP BY sid, channel
),
funnel AS (
  SELECT
    properties.$session_id AS sid,
    countIf(event = 'booking_initial_assessment_clicked') AS appts,
    countIf(event = 'booking_form_submitted')             AS forms,
    countIf(event = 'booking_payment_completed')          AS purchases
  FROM events
  WHERE event IN ('booking_initial_assessment_clicked',
                  'booking_form_submitted',
                  'booking_payment_completed')
    AND timestamp >= toDate('{AGO30}')
    AND timestamp < toDate('{YEST}') + INTERVAL 1 DAY
  GROUP BY sid
)
SELECT
  s.channel,
  count(DISTINCT s.sid)           AS sessions,
  countIf(f.appts > 0)            AS appts,
  countIf(f.forms > 0)            AS forms,
  countIf(f.purchases > 0)        AS purchases
FROM sessions s
LEFT JOIN funnel f ON s.sid = f.sid
GROUP BY s.channel
ORDER BY sessions DESC
""")
for c in channels:
    for k in ("sessions","appts","forms","purchases"): c[k] = int(c.get(k,0))

print("\n=== PostHog: Daily all-channel funnel 14d ===", flush=True)
daily14_raw = posthog_sql("daily14", f"""
SELECT
  toDate(timestamp) AS date,
  countDistinctIf(properties.$session_id, event = '$pageview') AS sessions,
  countIf(event = 'booking_initial_assessment_clicked')         AS appts,
  countIf(event = 'booking_form_submitted')                     AS forms,
  countIf(event = 'booking_payment_completed')                  AS purchases
FROM events
WHERE timestamp >= toDate('{AGO14}')
  AND timestamp < toDate('{YEST}') + INTERVAL 1 DAY
  AND event IN ('$pageview','booking_initial_assessment_clicked',
                'booking_form_submitted','booking_payment_completed')
GROUP BY date
ORDER BY date
""")
daily14 = [
    {"date":str(r.get("date",""))[:10], "sessions":int(r.get("sessions",0)),
     "appts":int(r.get("appts",0)), "forms":int(r.get("forms",0)),
     "purchases":int(r.get("purchases",0))}
    for r in daily14_raw
]

# ════════════════════════════════════════════════════════════════════════════
# STEP 4: Write data.json
# ════════════════════════════════════════════════════════════════════════════
output = {
    "generated_at": today.isoformat(),
    "date":         YEST,
    "campaigns":    campaigns,
    "daily":        daily7,
    "agg14":        agg14,
    "agg30":        agg30,
    "channels":     channels,
    "daily14":      daily14,
}
with open("data.json","w") as f:
    json.dump(output, f, indent=2)

print(f"\n✅ data.json written — {len(campaigns)} campaigns, {len(daily7)} daily rows, "
      f"{len(channels)} channels, {len(daily14)} daily14 rows", flush=True)
