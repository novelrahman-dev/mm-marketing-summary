#!/usr/bin/env python3
"""
Modern Menopause — Daily Data Fetcher
Runs via GitHub Actions each morning at 9am ET.
Fetches Windsor.ai + PostHog data and writes data.json to repo root.
"""

import json, os, sys, urllib.request, urllib.parse
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

WINDSOR_API_KEY  = os.environ["WINDSOR_API_KEY"]
POSTHOG_API_KEY  = os.environ["POSTHOG_API_KEY"]
POSTHOG_PROJECT  = os.environ["POSTHOG_PROJECT_ID"]
GA_ACCOUNT       = "230-778-8239"
PURCHASE_CONV    = "[gtag] purchase"

# ── WINDSOR ──────────────────────────────────────────────────────────────────
def windsor_get(fields, date_from, date_to):
    """Call Windsor REST API. Fields is a list of field IDs."""
    params = urllib.parse.urlencode({
        "api_key":    WINDSOR_API_KEY,
        "connector":  "google_ads",
        "account_id": GA_ACCOUNT,
        "date_from":  date_from,
        "date_to":    date_to,
        "fields":     ",".join(fields),
    })
    url = f"https://connectors.windsor.ai/google_ads?{params}"
    print(f"  Windsor: {date_from} to {date_to} → {fields[:3]}…")
    req = urllib.request.Request(url, headers={"Accept": "application/json"})
    with urllib.request.urlopen(req, timeout=60) as r:
        body = r.read()
    data = json.loads(body)
    # Windsor returns {"data": [...]} or just [...]
    if isinstance(data, dict):
        return data.get("data", [])
    return data

def safe_float(v):
    try: return float(v or 0)
    except: return 0.0

# ── CAMPAIGN METRICS (yesterday) ─────────────────────────────────────────────
print("Fetching Windsor campaign metrics (yesterday)...")
raw_camp = windsor_get(
    ["campaign", "campaign_type", "impressions", "clicks", "cost",
     "search_top_impression_share", "roas"],
    YEST, YEST
)

print("Fetching Windsor campaign conversions (yesterday)...")
raw_conv = windsor_get(
    ["campaign", "conversion_action_name", "conversions"],
    YEST, YEST
)

print("Fetching Windsor daily metrics (7d)...")
raw_daily = windsor_get(
    ["date", "impressions", "clicks", "cost", "search_top_impression_share"],
    AGO7, YEST
)

print("Fetching Windsor daily conversions (7d)...")
raw_daily_conv = windsor_get(
    ["date", "conversion_action_name", "conversions"],
    AGO7, YEST
)

print("Fetching Windsor 14d aggregate...")
raw_14      = windsor_get(["impressions", "clicks", "cost", "roas"], AGO14, YEST)
raw_14_conv = windsor_get(["conversion_action_name", "conversions"], AGO14, YEST)

print("Fetching Windsor 30d aggregate...")
raw_30      = windsor_get(["impressions", "clicks", "cost", "roas"], AGO30, YEST)
raw_30_conv = windsor_get(["conversion_action_name", "conversions"], AGO30, YEST)

# ── PARSE CAMPAIGNS ──────────────────────────────────────────────────────────
camp_map = {}
for r in raw_camp:
    name = r.get("campaign", "")
    if not name: continue
    ctype = r.get("campaign_type", "")
    camp_map[name] = {
        "campaign": name,
        "type": "PERFORMANCE_MAX" if "PERFORMANCE_MAX" in ctype.upper() or "pmax" in name.lower() else "SEARCH",
        "impr":   safe_float(r.get("impressions")),
        "clicks": safe_float(r.get("clicks")),
        "cost":   safe_float(r.get("cost")),
        "top_is": safe_float(r.get("search_top_impression_share")),
        "roas":   safe_float(r.get("roas")),
        "purch": 0.0, "appts": 0, "forms": 0, "ph_purch": 0
    }

for r in raw_conv:
    name = r.get("campaign", "")
    if r.get("conversion_action_name", "") == PURCHASE_CONV and name in camp_map:
        camp_map[name]["purch"] += safe_float(r.get("conversions"))

campaigns = list(camp_map.values())

# ── PARSE DAILY ───────────────────────────────────────────────────────────────
daily_map = {}
for r in raw_daily:
    d = str(r.get("date", ""))[:10]
    if not d: continue
    daily_map[d] = {
        "date": d,
        "impr":   safe_float(r.get("impressions")),
        "clicks": safe_float(r.get("clicks")),
        "cost":   safe_float(r.get("cost")),
        "top_is": safe_float(r.get("search_top_impression_share")),
        "purch": 0.0, "appts": 0, "forms": 0, "ph_purch": 0
    }
for r in raw_daily_conv:
    d = str(r.get("date", ""))[:10]
    if r.get("conversion_action_name", "") == PURCHASE_CONV and d in daily_map:
        daily_map[d]["purch"] += safe_float(r.get("conversions"))
daily = sorted(daily_map.values(), key=lambda x: x["date"])

# ── AGGREGATES ───────────────────────────────────────────────────────────────
def agg_conv(rows):
    return sum(safe_float(r.get("conversions")) for r in rows if r.get("conversion_action_name","") == PURCHASE_CONV)

def agg_metrics(rows):
    impr=clicks=cost=roas_s=cnt=0.0
    for r in rows:
        impr+=safe_float(r.get("impressions")); clicks+=safe_float(r.get("clicks"))
        cost+=safe_float(r.get("cost"))
        if safe_float(r.get("roas",0))>0: roas_s+=safe_float(r.get("roas")); cnt+=1
    return {"impr":impr,"clicks":clicks,"cost":cost,"roas":roas_s/cnt if cnt else 0}

agg14 = {**agg_metrics(raw_14),  "purch": agg_conv(raw_14_conv)}
agg30 = {**agg_metrics(raw_30),  "purch": agg_conv(raw_30_conv)}

# ── POSTHOG ──────────────────────────────────────────────────────────────────
def posthog_sql(query):
    url = f"https://app.posthog.com/api/projects/{POSTHOG_PROJECT}/query/"
    payload = json.dumps({"query": {"kind": "HogQLQuery", "query": query}}).encode()
    req = urllib.request.Request(url, data=payload, headers={
        "Content-Type": "application/json",
        "Authorization": f"Bearer {POSTHOG_API_KEY}"
    })
    with urllib.request.urlopen(req, timeout=90) as r:
        data = json.loads(r.read())
    cols = data.get("columns", [])
    return [dict(zip(cols, row)) for row in data.get("results", [])]

print("Fetching PostHog daily funnel (7d Google Paid)...")
ph_daily = posthog_sql(f"""
SELECT toDate(timestamp) AS day, event, count() AS cnt
FROM events
WHERE timestamp >= toDate('{AGO7}') AND timestamp < toDate('{YEST}') + INTERVAL 1 DAY
  AND properties.utm_source = 'google_paid'
  AND event IN ('booking_initial_assessment_clicked','booking_form_submitted','booking_payment_completed')
GROUP BY day, event ORDER BY day
""")

ph_map = {}
for r in ph_daily:
    d = str(r.get("day",""))[:10]; e = r.get("event",""); cnt = int(r.get("cnt",0))
    if d not in ph_map: ph_map[d] = {"appts":0,"forms":0,"ph_purch":0}
    if e=="booking_initial_assessment_clicked": ph_map[d]["appts"]+=cnt
    elif e=="booking_form_submitted":           ph_map[d]["forms"]+=cnt
    elif e=="booking_payment_completed":        ph_map[d]["ph_purch"]+=cnt

for d in daily:
    if d["date"] in ph_map: d.update(ph_map[d["date"]])

print("Fetching PostHog channel funnel (30d)...")
channels = posthog_sql(f"""
WITH cs AS (
  SELECT person_id, properties.$session_id AS sid,
    multiIf(
      properties.utm_source='google_paid','Google Paid',
      properties.utm_source IN ('fb','facebook'),'Meta Paid',
      properties.utm_medium IN ('Facebook_Mobile_Feed','Facebook_Desktop_Feed'),'Meta Paid',
      properties.utm_source='ig','Meta Paid',
      properties.utm_source='paid_social_media','Meta Paid',
      properties.utm_source='bing','Bing Paid',
      properties.utm_source='chatgpt.com','ChatGPT',
      properties.utm_source IS NOT NULL AND properties.utm_source!='','Other Paid',
      properties.$referring_domain='$direct' OR properties.$referring_domain IS NULL,'Direct',
      properties.$referring_domain LIKE '%google%','Organic Search',
      properties.$referring_domain LIKE '%bing%','Organic Search',
      properties.$referring_domain LIKE '%yahoo%','Organic Search',
      properties.$referring_domain LIKE '%duckduckgo%','Organic Search',
      properties.$referring_domain LIKE '%facebook%' OR properties.$referring_domain LIKE '%instagram%','Organic Social',
      'Referral'
    ) AS channel
  FROM events WHERE timestamp>=toDate('{AGO30}') AND timestamp<toDate('{YEST}')+INTERVAL 1 DAY AND event='$pageview'
  GROUP BY person_id, sid, channel
),
fe AS (
  SELECT person_id, properties.$session_id AS sid,
    countIf(event='booking_initial_assessment_clicked') AS appts,
    countIf(event='booking_form_submitted') AS forms,
    countIf(event='booking_payment_completed') AS purchases
  FROM events WHERE timestamp>=toDate('{AGO30}') AND timestamp<toDate('{YEST}')+INTERVAL 1 DAY
    AND event IN ('booking_initial_assessment_clicked','booking_form_submitted','booking_payment_completed')
  GROUP BY person_id, sid
)
SELECT cs.channel, count(DISTINCT cs.sid) AS sessions,
  countIf(fe.appts>0) AS appts, countIf(fe.forms>0) AS forms, countIf(fe.purchases>0) AS purchases
FROM cs LEFT JOIN fe ON cs.person_id=fe.person_id AND cs.sid=fe.sid
GROUP BY cs.channel ORDER BY sessions DESC LIMIT 20
""")
for c in channels:
    for k in ("sessions","appts","forms","purchases"): c[k]=int(c.get(k,0))

print("Fetching PostHog daily all-channel funnel (14d)...")
daily14_raw = posthog_sql(f"""
SELECT toDate(timestamp) AS date,
  count(DISTINCT if(event='$pageview', properties.$session_id, null)) AS sessions,
  countIf(event='booking_initial_assessment_clicked') AS appts,
  countIf(event='booking_form_submitted') AS forms,
  countIf(event='booking_payment_completed') AS purchases
FROM events
WHERE timestamp>=toDate('{AGO14}') AND timestamp<toDate('{YEST}')+INTERVAL 1 DAY
  AND event IN ('$pageview','booking_initial_assessment_clicked','booking_form_submitted','booking_payment_completed')
GROUP BY date ORDER BY date LIMIT 30
""")
daily14 = [{"date":str(r.get("date",""))[:10],"sessions":int(r.get("sessions",0)),
            "appts":int(r.get("appts",0)),"forms":int(r.get("forms",0)),
            "purchases":int(r.get("purchases",0))} for r in daily14_raw]

# ── WRITE data.json ───────────────────────────────────────────────────────────
output = {
    "generated_at": today.isoformat(),
    "date": YEST,
    "campaigns": campaigns,
    "daily": daily,
    "agg14": agg14,
    "agg30": agg30,
    "channels": channels,
    "daily14": daily14,
}
with open("data.json","w") as f:
    json.dump(output, f, indent=2)

print(f"✅ data.json written — {len(campaigns)} campaigns, {len(daily)} days, {len(channels)} channels, {len(daily14)} daily14 rows")
