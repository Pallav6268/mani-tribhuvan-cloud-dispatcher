import csv
import datetime
import html
import io
import json
import os
import smtplib
import threading
import time
import urllib.request
from http.server import HTTPServer, BaseHTTPRequestHandler
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from email.utils import formatdate, make_msgid

# Indian Standard Time (UTC+5:30)
IST = datetime.timezone(datetime.timedelta(hours=5, minutes=30))

SHEET_EXPORT_URL = os.environ.get(
    "SHEET_EXPORT_URL",
    "https://docs.google.com/spreadsheets/d/1cZ7N52nVw9yy3QpsMh1Kra9-yk7mZlm9p0IPz4XYTCw/export?format=csv&gid=0"
)

SENDER_EMAIL = os.environ.get("SENDER_EMAIL", "mtrwa.office@gmail.com")
APP_PASSWORD = os.environ.get("APP_PASSWORD", "ybpmuratzunwiejv")

APPOINTED_RECIPIENTS = [
    "deepakdhawan@mani-group.com",
    "Pallav@mani-group.com",
    "mtrwa.office@gmail.com"
]

MEMBERS_CSV_URL = os.environ.get(
    "MEMBERS_CSV_URL",
    "https://docs.google.com/spreadsheets/d/1cZ7N52nVw9yy3QpsMh1Kra9-yk7mZlm9p0IPz4XYTCw/export?format=csv&gid=1248655544"
)

TOTAL_FLATS = 465

dispatched = {"14LD", "4LA", "6LB", "101", "11CD", "12LB", "1JB", "10NA", "2KC", "14BB", "3JC", "4DC", "13DB", "3BB", "13AA", "9CA", "1KC", "8CC", "5CC"}

# Known revisions history (Flat -> previous details)
KNOWN_REVISIONS = {
    "10NA": {
        "prev_time": "01 Sep 2026, 04:30 PM IST",
        "prev_vote": "Option 2: Suggestions",
        "prev_comment": "Initial suggestions submitted regarding common area handover and warranty terms.",
        "delta": "+2 hrs 41 mins"
    }
}

# Shared dashboard cache
dashboard_lock = threading.Lock()
dashboard_cache = {
    "total_votes": 0,
    "opt1": 0,
    "opt2": 0,
    "opt3": 0,
    "locked": 0,
    "open_to_revise": 0,
    "hourly": {},
    "all_ballots": [],
    "latest_vote_time": "",
    "latest_vote_flat": "",
    "last_updated": "Initializing..."
}

def get_current_ist_time_str():
    return datetime.datetime.now(IST).strftime("%d %b %Y, %I:%M:%S %p IST")

def load_members_from_cloud():
    members = {}
    try:
        req = urllib.request.Request(MEMBERS_CSV_URL, headers={"User-Agent": "Mozilla/5.0"})
        with urllib.request.urlopen(req, timeout=15) as resp:
            content = resp.read().decode("utf-8")
            reader = list(csv.reader(io.StringIO(content)))
            for r in reader[1:]:
                if len(r) >= 3:
                    f_no = r[1].strip().upper()
                    if f_no:
                        members[f_no] = {
                            "name": r[0].strip(),
                            "email": r[2].strip()
                        }
        print(f"[+] Loaded {len(members)} society members dynamically from Google Sheets!")
    except Exception as e:
        print(f"[-] Could not load members from cloud sheet: {e}")
    return members

def update_dashboard_data(rows, members_map):
    if not rows or len(rows) <= 1:
        return

    opt1 = opt2 = opt3 = 0
    locked = open_to_revise = 0
    hourly_dt = {}
    all_ballots = []
    latest_time = ""
    latest_flat = ""

    for r in rows[1:]:
        if len(r) < 4:
            continue
        ts_str = r[0].strip()
        email = r[1].strip()
        flat_no = r[2].strip().upper()
        vote = r[3].strip()
        comment = r[4].strip() if len(r) > 4 else ""
        ip = r[5].strip() if len(r) > 5 else "Not Detected"
        device = r[6].strip() if len(r) > 6 else "Web Browser"
        status = r[7].strip() if len(r) > 7 else "LOCKED"
        dispatch_status = r[8].strip().upper() if len(r) > 8 else ""

        if not flat_no:
            continue

        if "Option 1" in vote:
            opt1 += 1
            cat = "opt1"
            badge_color = "#059669"
            badge_bg = "#ecfdf5"
            vote_label = "Option 1: Agree"
        elif "Option 2" in vote:
            opt2 += 1
            cat = "opt2"
            badge_color = "#d97706"
            badge_bg = "#fffbeb"
            vote_label = "Option 2: Suggestions"
        elif "Option 3" in vote:
            opt3 += 1
            cat = "opt3"
            badge_color = "#dc2626"
            badge_bg = "#fef2f2"
            vote_label = "Option 3: Disagree"
        else:
            cat = "other"
            badge_color = "#475569"
            badge_bg = "#f1f5f9"
            vote_label = vote

        if status.upper() == "LOCKED":
            locked += 1
        else:
            open_to_revise += 1

        latest_time = ts_str
        latest_flat = flat_no

        try:
            dt = datetime.datetime.strptime(ts_str, "%d/%m/%Y %H:%M:%S")
            bucket_dt = dt.replace(minute=0, second=0, microsecond=0)
            hourly_dt[bucket_dt] = hourly_dt.get(bucket_dt, 0) + 1
        except Exception:
            pass

        is_revised = flat_no in KNOWN_REVISIONS
        if is_revised:
            cat += " revised"

        m_info = members_map.get(flat_no, {})
        m_name = m_info.get("name", "Society Member")
        effective_email = email or m_info.get("email", "")

        all_ballots.append({
            "flat": flat_no,
            "name": m_name,
            "email": effective_email,
            "time": ts_str,
            "vote": vote_label,
            "badge_color": badge_color,
            "badge_bg": badge_bg,
            "comment": comment,
            "ip": ip,
            "device": device,
            "status": status,
            "is_revised": is_revised,
            "revision_data": KNOWN_REVISIONS.get(flat_no, None),
            "category": cat
        })

    # Sort hourly strictly chronologically
    sorted_hourly = {}
    for b_dt in sorted(hourly_dt.keys()):
        label = b_dt.strftime("%d %b (%I:00 %p)")
        sorted_hourly[label] = hourly_dt[b_dt]

    all_ballots.reverse() # Newest first

    with dashboard_lock:
        dashboard_cache["total_votes"] = len(rows) - 1
        dashboard_cache["opt1"] = opt1
        dashboard_cache["opt2"] = opt2
        dashboard_cache["opt3"] = opt3
        dashboard_cache["locked"] = locked
        dashboard_cache["open_to_revise"] = open_to_revise
        dashboard_cache["hourly"] = sorted_hourly
        dashboard_cache["all_ballots"] = all_ballots
        dashboard_cache["latest_vote_time"] = latest_time
        dashboard_cache["latest_vote_flat"] = latest_flat
        dashboard_cache["last_updated"] = get_current_ist_time_str()

def generate_excel_csv(query_path):
    with dashboard_lock:
        ballots = list(dashboard_cache.get("all_ballots", []))

    filter_opt1 = "opt1" in query_path
    
    csv_buf = io.StringIO()
    csv_buf.write("\ufeff") # Excel UTF-8 BOM
    writer = csv.writer(csv_buf)
    
    writer.writerow([
        "S.No",
        "Flat No.",
        "Resident Name",
        "Email Address",
        "Voting Choice",
        "Submission Timestamp (IST)",
        "Comments / Reasons",
        "Lock Status"
    ])

    count = 0
    # Chronological or registry order
    for b in reversed(ballots):
        if filter_opt1 and "Option 1" not in b["vote"]:
            continue
        count += 1
        comment_clean = b["comment"] if b["comment"] and b["comment"].lower() != "none" else ""
        writer.writerow([
            count,
            b["flat"],
            b["name"],
            b["email"],
            b["vote"],
            b["time"],
            comment_clean,
            b["status"]
        ])

    return csv_buf.getvalue().encode("utf-8")

def generate_dashboard_html():
    with dashboard_lock:
        data = dict(dashboard_cache)

    total = data.get("total_votes", 0)
    opt1 = data.get("opt1", 0)
    opt2 = data.get("opt2", 0)
    opt3 = data.get("opt3", 0)
    locked = data.get("locked", 0)
    hourly = data.get("hourly", {})
    ballots = data.get("all_ballots", [])
    updated = data.get("last_updated", get_current_ist_time_str())
    latest_time = data.get("latest_vote_time", "")
    latest_flat = data.get("latest_vote_flat", "")

    pct1 = f"{(opt1 / total * 100):.1f}%" if total > 0 else "0.0%"
    pct2 = f"{(opt2 / total * 100):.1f}%" if total > 0 else "0.0%"
    pct3 = f"{(opt3 / total * 100):.1f}%" if total > 0 else "0.0%"
    turnout_pct = f"{(total / TOTAL_FLATS * 100):.1f}%"

    opt1_bar = f"{(opt1 / TOTAL_FLATS * 100):.1f}%"
    opt2_bar = f"{(opt2 / TOTAL_FLATS * 100):.1f}%"
    opt3_bar = f"{(opt3 / TOTAL_FLATS * 100):.1f}%"

    max_hourly = max(hourly.values()) if hourly else 1
    hourly_bars_html = ""
    for h_label, h_count in hourly.items():
        height_pct = int((h_count / max_hourly) * 100) if max_hourly > 0 else 20
        height_pct = max(height_pct, 18)
        hourly_bars_html += f"""
        <div style="flex:1; display:flex; flex-direction:column; align-items:center; justify-content:flex-end; min-width:65px; height:100%;">
          <span style="font-size:11px; font-weight:800; color:#4f46e5; margin-bottom:4px;">{h_count}</span>
          <div style="width:100%; background:#6366f1; border-radius:6px 6px 0 0; height:{height_pct}%;"></div>
          <span style="font-size:10px; font-weight:700; color:#475569; margin-top:6px; white-space:nowrap;">{html.escape(h_label)}</span>
        </div>
        """

    cards_html = ""
    if not ballots:
        cards_html = "<div style='color:#64748b; font-size:13px; padding:30px; text-align:center;'>No ballots recorded yet.</div>"
    else:
        for b in ballots:
            if b.get("is_revised") and b.get("revision_data"):
                rev = b["revision_data"]
                cards_html += f"""
                <div class="card-item {b['category']}" style="border:2px solid #d8b4fe; background:linear-gradient(135deg, rgba(243,232,255,0.4), #ffffff); border-radius:16px; padding:18px; margin-bottom:14px; box-shadow:0 2px 4px rgba(0,0,0,0.03);">
                  <div style="display:flex; justify-content:space-between; align-items:center; flex-wrap:wrap; gap:10px; padding-bottom:12px; margin-bottom:12px; border-bottom:1px solid #f3e8ff;">
                    <div style="display:flex; align-items:center; gap:10px;">
                      <div style="width:40px; height:40px; border-radius:10px; background:#ede9fe; color:#6b21a8; display:flex; align-items:center; justify-content:center; font-weight:800; font-size:13px; border:1px solid #ddd6fe;">
                        {html.escape(b['flat'])}
                      </div>
                      <div>
                        <div style="font-size:16px; font-weight:800; color:#0f172a;">Flat {html.escape(b['flat'])} <span style="font-size:13px; font-weight:500; color:#64748b;">• {html.escape(b['name'])}</span></div>
                        <div style="font-size:11px; color:#94a3b8; font-family:monospace;">{html.escape(b['email'])}</div>
                      </div>
                    </div>
                    <div style="display:flex; align-items:center; gap:8px;">
                      <span style="font-size:11px; font-weight:800; padding:4px 10px; border-radius:20px; background:#f3e8ff; color:#6b21a8; border:1px solid #d8b4fe;">
                        🔄 REVISED VOTE
                      </span>
                      <div style="background:#4c1d95; color:#ffffff; padding:4px 12px; border-radius:8px; font-size:12px; font-weight:700; font-family:monospace;">
                        🕒 Final Vote: {html.escape(b['time'])}
                      </div>
                    </div>
                  </div>

                  <div style="background:#ffffff; border:1px solid #e9d5ff; border-radius:12px; padding:16px; box-shadow:0 1px 2px rgba(0,0,0,0.02);">
                    <div style="font-size:11px; font-weight:800; color:#64748b; text-transform:uppercase; letter-spacing:0.5px; margin-bottom:12px; display:flex; justify-content:space-between;">
                      <span>Audit Trail: Opinion Evolution Journey</span>
                      <span style="background:#f1f5f9; color:#475569; padding:2px 6px; border-radius:4px; font-weight:600;">Delta: {rev.get('delta', 'Updated')}</span>
                    </div>
                    
                    <div style="position:relative; padding-left:22px; padding-bottom:14px; border-left:2px solid #fbbf24;">
                      <div style="position:absolute; left:-7px; top:0; width:12px; height:12px; border-radius:50%; background:#f59e0b; border:2px solid #ffffff;"></div>
                      <div style="display:flex; justify-content:space-between; align-items:center; flex-wrap:wrap; gap:6px;">
                        <span style="font-size:11px; font-weight:800; color:#b45309; background:#fef3c7; padding:2px 8px; border-radius:6px; border:1px solid #fde68a;">
                          Previous Submission: {html.escape(rev.get('prev_vote', 'Option 2'))}
                        </span>
                        <span style="font-size:11px; font-weight:700; color:#475569; font-family:monospace;">🕒 {html.escape(rev.get('prev_time', 'Earlier'))}</span>
                      </div>
                      <div style="font-size:12px; color:#475569; background:#fffbeb; padding:10px; border-radius:8px; border:1px solid #fef3c7; margin-top:8px; font-style:italic;">
                        "{html.escape(rev.get('prev_comment', 'Initial comments'))}"
                      </div>
                    </div>

                    <div style="position:relative; padding-left:22px; padding-bottom:8px; border-left:2px dashed #c084fc;">
                      <div style="position:absolute; left:-5px; top:4px; width:8px; height:8px; border-radius:50%; background:#a855f7;"></div>
                      <span style="font-size:11px; font-weight:800; color:#7e22ce; background:#faf5ff; padding:3px 10px; border-radius:20px; border:1px solid #e9d5ff; display:inline-block;">
                        ⬇️ Member Changed Stance & Resubmitted Ballot
                      </span>
                    </div>

                    <div style="position:relative; padding-left:22px; pt:6px; border-left:2px solid #ef4444;">
                      <div style="position:absolute; left:-7px; top:4px; width:12px; height:12px; border-radius:50%; background:#ef4444; border:2px solid #ffffff;"></div>
                      <div style="display:flex; justify-content:space-between; align-items:center; flex-wrap:wrap; gap:6px;">
                        <span style="font-size:11px; font-weight:800; color:#b91c1c; background:#fee2e2; padding:2px 8px; border-radius:6px; border:1px solid #fca5a5;">
                          Current Official Ballot: {html.escape(b['vote'])}
                        </span>
                        <span style="font-size:11px; font-weight:700; color:#b91c1c; font-family:monospace;">🕒 {html.escape(b['time'])}</span>
                      </div>
                      <div style="font-size:13px; color:#0f172a; background:#fef2f2; padding:10px; border-radius:8px; border:1px solid #fecaca; margin-top:8px; font-weight:500; white-space:pre-line;">
{html.escape(b['comment'])}
                      </div>
                    </div>
                  </div>

                  <div style="margin-top:10px; display:flex; justify-content:space-between; font-size:11px; color:#94a3b8;">
                    <span>Verified Society Member • Token Authenticated</span>
                    <span style="font-family:monospace;">Lock Status: {html.escape(b['status'])}</span>
                  </div>
                </div>
                """
            else:
                has_com = bool(b.get("comment") and b["comment"].lower() != "none" and b["comment"].strip())
                comment_box = ""
                if has_com:
                    comment_box = f"""
                    <div style="font-size:13px; color:#1e293b; background:#f8fafc; padding:12px; border-radius:10px; border:1px solid #f1f5f9; line-height:1.6; margin-top:10px; white-space:pre-line;">
{html.escape(b['comment'])}
                    </div>
                    """
                else:
                    comment_box = f"""
                    <div style="font-size:12px; color:#059669; background:#ecfdf5; padding:8px 12px; border-radius:8px; border:1px solid #a7f3d0; margin-top:8px; font-weight:600;">
                      ✅ Voted in agreement with Draft Settlement Agreement. Response recorded and locked.
                    </div>
                    """

                cards_html += f"""
                <div class="card-item {b['category']}" style="background:#ffffff; border:1px solid #e2e8f0; border-radius:14px; padding:16px; margin-bottom:12px; box-shadow:0 1px 2px rgba(0,0,0,0.03);">
                  <div style="display:flex; justify-content:space-between; align-items:center; flex-wrap:wrap; gap:8px; padding-bottom:8px; border-bottom:1px solid #f1f5f9;">
                    <div style="display:flex; align-items:center; gap:10px;">
                      <div style="width:36px; height:36px; border-radius:8px; background:#f1f5f9; color:#1e293b; display:flex; align-items:center; justify-content:center; font-weight:800; font-size:12px; border:1px solid #e2e8f0;">
                        {html.escape(b['flat'])}
                      </div>
                      <div>
                        <div style="font-size:15px; font-weight:800; color:#0f172a;">Flat {html.escape(b['flat'])} <span style="font-size:13px; font-weight:500; color:#64748b;">• {html.escape(b['name'])}</span></div>
                        <div style="font-size:11px; color:#94a3b8; font-family:monospace;">{html.escape(b['email'])}</div>
                      </div>
                    </div>
                    <div style="display:flex; align-items:center; gap:8px;">
                      <span style="font-size:11px; font-weight:700; padding:3px 10px; border-radius:6px; background:{b['badge_bg']}; color:{b['badge_color']}; border:1px solid {b['badge_color']}30;">
                        {html.escape(b['vote'])}
                      </span>
                      <div style="background:#f8fafc; color:#334155; border:1px solid #e2e8f0; padding:3px 8px; border-radius:6px; font-size:11px; font-weight:700; font-family:monospace;">
                        🕒 {html.escape(b['time'])}
                      </div>
                      <span style="font-size:10px; font-weight:700; padding:2px 6px; border-radius:4px; background:#f1f5f9; color:#475569;">{html.escape(b['status'])}</span>
                    </div>
                  </div>

                  {comment_box}
                </div>
                """

    return f"""<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  <meta http-equiv="refresh" content="30">
  <title>MANI TRIBHUVAN – Settlement Poll Analytics</title>
  <style>
    * {{ box-sizing: border-box; margin: 0; padding: 0; }}
    body {{ font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, Helvetica, Arial, sans-serif; background-color: #f1f5f9; color: #0f172a; padding: 20px; line-height: 1.5; }}
    .container {{ max-width: 1100px; margin: 0 auto; }}
    .card {{ background: #ffffff; border: 1px solid #e2e8f0; border-radius: 16px; padding: 24px; margin-bottom: 20px; box-shadow: 0 1px 3px rgba(0,0,0,0.04); }}
    .header-box {{ background: linear-gradient(135deg, #0f172a, #1e1b4b); color: #ffffff; border-radius: 20px; padding: 24px 28px; margin-bottom: 20px; box-shadow: 0 4px 12px rgba(15,23,42,0.15); }}
    .grid-4 {{ display: grid; grid-template-columns: repeat(auto-fit, minmax(220px, 1fr)); gap: 16px; margin-bottom: 20px; }}
    .metric-card {{ background: #ffffff; border: 1px solid #e2e8f0; border-radius: 14px; padding: 18px; box-shadow: 0 1px 2px rgba(0,0,0,0.03); }}
    .filter-btn {{ padding: 6px 12px; font-size: 12px; font-weight: 700; border-radius: 10px; border: 1px solid #e2e8f0; background: #ffffff; color: #475569; cursor: pointer; transition: all 0.2s; }}
    .filter-btn:hover {{ background: #f8fafc; }}
    .filter-btn.active {{ background: #0f172a; color: #ffffff; border-color: #0f172a; }}
    .search-input {{ padding: 7px 12px; font-size: 12px; border-radius: 10px; border: 1px solid #cbd5e1; background: #f8fafc; outline: none; width: 200px; }}
    .search-input:focus {{ border-color: #6366f1; background: #ffffff; }}
    .btn-action {{ color: #ffffff; text-decoration: none; padding: 8px 14px; font-size: 12px; font-weight: 700; border-radius: 10px; display: inline-flex; align-items: center; gap: 6px; transition: opacity 0.2s; border: none; cursor: pointer; }}
    .btn-action:hover {{ opacity: 0.9; }}
    @media print {{
      body {{ background: #ffffff; padding: 0; }}
      .no-print {{ display: none !important; }}
      .card {{ border: none; box-shadow: none; padding: 0; }}
    }}
  </style>
</head>
<body>
  <div class="container">
    
    <!-- Top Header -->
    <div class="header-box">
      <div style="display:flex; justify-content:space-between; align-items:center; flex-wrap:wrap; gap:16px;">
        <div>
          <div style="font-size:11px; font-weight:800; color:#a7f3d0; text-transform:uppercase; letter-spacing:1px; margin-bottom:4px;">
            Mani Tribhuvan Residents Welfare Association
          </div>
          <h1 style="font-size:24px; font-weight:800; color:#ffffff; letter-spacing:-0.5px;">MANI TRIBHUVAN</h1>
          <p style="font-size:13px; color:#cbd5e1; margin-top:2px;">Draft Settlement Agreement • Member Opinion Poll Analytics</p>
        </div>
        <div style="display:flex; align-items:center; gap:8px; flex-wrap:wrap;" class="no-print">
          <!-- WhatsApp Copy Button (Clean text only) -->
          <button onclick="copyWhatsAppSummary()" class="btn-action" style="background:#059669;">
            <span id="copy-btn-text">📲 Copy WhatsApp Update</span>
          </button>
          
          <!-- Native Server Download of Excel -->
          <a href="/export/excel" download="Mani_Tribhuvan_Poll_Registry.csv" class="btn-action" style="background:#0f766e;">
            📊 Export All to Excel (.csv)
          </a>

          <!-- Native Print to PDF -->
          <button onclick="window.print()" class="btn-action" style="background:#4f46e5;">
            📄 Save as PDF
          </button>
        </div>
      </div>
    </div>

    <!-- Society Turnout & Quorum Progress Bar -->
    <div class="card" style="padding:16px 20px;">
      <div style="display:flex; justify-content:space-between; align-items:center; flex-wrap:wrap; gap:10px; margin-bottom:10px;">
        <div>
          <strong style="font-size:14px; color:#0f172a;">Society Turnout & Quorum Progress</strong>
          <span style="font-size:12px; color:#64748b; margin-left:8px;">Target: 465 Member Flats</span>
        </div>
        <div>
          <span style="font-size:12px; font-weight:800; color:#4f46e5; background:#eef2ff; padding:3px 10px; border-radius:6px; border:1px solid #e0e7ff;">
            {total} of {TOTAL_FLATS} Flats ({turnout_pct} Turnout)
          </span>
        </div>
      </div>
      
      <div style="width:100%; height:12px; background:#f1f5f9; border-radius:6px; overflow:hidden; display:flex; border:1px solid #e2e8f0;">
        <div style="width:{opt1_bar}; background:#10b981;" title="Option 1: {opt1} votes ({pct1})"></div>
        <div style="width:{opt2_bar}; background:#f59e0b;" title="Option 2: {opt2} votes ({pct2})"></div>
        <div style="width:{opt3_bar}; background:#ef4444;" title="Option 3: {opt3} votes ({pct3})"></div>
      </div>
      <div style="display:flex; justify-content:space-between; font-size:11px; color:#64748b; margin-top:8px;">
        <span style="display:inline-flex; align-items:center; gap:4px; font-weight:700; color:#047857;"><span style="width:8px; height:8px; border-radius:50%; background:#10b981; display:inline-block;"></span> Option 1: Agree ({opt1})</span>
        <span style="display:inline-flex; align-items:center; gap:4px; font-weight:700; color:#b45309;"><span style="width:8px; height:8px; border-radius:50%; background:#f59e0b; display:inline-block;"></span> Option 2: Suggestions ({opt2})</span>
        <span style="display:inline-flex; align-items:center; gap:4px; font-weight:700; color:#b91c1c;"><span style="width:8px; height:8px; border-radius:50%; background:#ef4444; display:inline-block;"></span> Option 3: Disagree ({opt3})</span>
        <span style="color:#94a3b8; font-weight:600;">Simple Majority Quorum: 233 Flats</span>
      </div>
    </div>

    <!-- 4 KPI Performance Cards -->
    <div class="grid-4 no-print">
      <div class="metric-card">
        <div style="font-size:11px; font-weight:800; color:#64748b; text-transform:uppercase;">Total Ballots Cast</div>
        <div style="font-size:32px; font-weight:900; color:#0f172a; margin-top:4px;">{total} <span style="font-size:14px; font-weight:600; color:#94a3b8;">/ {TOTAL_FLATS}</span></div>
        <div style="font-size:11px; color:#059669; font-weight:700; background:#ecfdf5; padding:2px 8px; border-radius:4px; display:inline-block; margin-top:6px;">{turnout_pct} Turnout</div>
        <div style="margin-top:10px; pt:8px; border-top:1px solid #f1f5f9; font-size:11px; color:#64748b;">
          🕒 Latest: <strong>{latest_time.split(' ')[-1] if latest_time else 'Active'}</strong> (Flat {latest_flat})
        </div>
      </div>

      <div class="metric-card" style="border-top: 4px solid #10b981;">
        <div style="display:flex; justify-content:space-between; align-items:center;">
          <span style="font-size:11px; font-weight:800; color:#059669; text-transform:uppercase;">Option 1: Agree</span>
          <span style="font-size:12px; font-weight:800; color:#059669; background:#ecfdf5; padding:2px 6px; border-radius:4px;">{pct1}</span>
        </div>
        <div style="font-size:32px; font-weight:900; color:#0f172a; margin-top:4px;">{opt1}</div>
        <div style="width:100%; height:4px; background:#e2e8f0; border-radius:2px; margin-top:8px; overflow:hidden;">
          <div style="width:{pct1}; height:100%; background:#10b981;"></div>
        </div>
        <div style="margin-top:10px; pt:8px; border-top:1px solid #f1f5f9; font-size:11px; color:#64748b;">
          Locked Invariant: <strong style="color:#059669;">Permanent ({opt1})</strong>
        </div>
      </div>

      <div class="metric-card" style="border-top: 4px solid #f59e0b;">
        <div style="display:flex; justify-content:space-between; align-items:center;">
          <span style="font-size:11px; font-weight:800; color:#d97706; text-transform:uppercase;">Option 2: Suggestions</span>
          <span style="font-size:12px; font-weight:800; color:#d97706; background:#fffbeb; padding:2px 6px; border-radius:4px;">{pct2}</span>
        </div>
        <div style="font-size:32px; font-weight:900; color:#0f172a; margin-top:4px;">{opt2}</div>
        <div style="width:100%; height:4px; background:#e2e8f0; border-radius:2px; margin-top:8px; overflow:hidden;">
          <div style="width:{pct2}; height:100%; background:#f59e0b;"></div>
        </div>
        <div style="margin-top:10px; pt:8px; border-top:1px solid #f1f5f9; font-size:11px; color:#64748b;">
          Locked Invariant: <strong style="color:#059669;">Permanent ({opt2})</strong>
        </div>
      </div>

      <div class="metric-card" style="border-top: 4px solid #ef4444;">
        <div style="display:flex; justify-content:space-between; align-items:center;">
          <span style="font-size:11px; font-weight:800; color:#dc2626; text-transform:uppercase;">Option 3: Disagree</span>
          <span style="font-size:12px; font-weight:800; color:#dc2626; background:#fef2f2; padding:2px 6px; border-radius:4px;">{pct3}</span>
        </div>
        <div style="font-size:32px; font-weight:900; color:#0f172a; margin-top:4px;">{opt3}</div>
        <div style="width:100%; height:4px; background:#e2e8f0; border-radius:2px; margin-top:8px; overflow:hidden;">
          <div style="width:{pct3}; height:100%; background:#ef4444;"></div>
        </div>
        <div style="margin-top:10px; pt:8px; border-top:1px solid #f1f5f9; font-size:11px; color:#64748b;">
          Locked Invariant: <strong style="color:#059669;">Permanent ({opt3})</strong>
        </div>
      </div>
    </div>

    <!-- Chronological Hourly Voting Velocity Chart -->
    <div class="card no-print">
      <div style="display:flex; justify-content:space-between; align-items:center; margin-bottom:16px;">
        <div>
          <h2 style="font-size:16px; font-weight:800; color:#0f172a;">Hourly Voting Velocity Timeline</h2>
          <p style="font-size:12px; color:#64748b;">Voting activity grouped strictly chronologically by 1-hour windows</p>
        </div>
        <span style="font-size:11px; font-weight:700; color:#4f46e5; background:#eef2ff; padding:3px 10px; border-radius:6px; border:1px solid #e0e7ff;">
          {len(hourly)} Active Hours
        </span>
      </div>

      <div style="overflow-x:auto; padding-bottom:8px;">
        <div style="display:flex; align-items:flex-end; gap:12px; min-width:680px; height:120px; border-bottom:1px solid #e2e8f0; padding-bottom:6px;">
          {hourly_bars_html}
        </div>
      </div>
    </div>

    <!-- Complete Resident Voting Registry & Audit Stream -->
    <div class="card">
      <div style="display:flex; justify-content:space-between; align-items:center; flex-wrap:wrap; gap:12px; padding-bottom:14px; margin-bottom:14px; border-bottom:1px solid #f1f5f9;" class="no-print">
        <div>
          <h2 style="font-size:16px; font-weight:800; color:#0f172a;">Member Ballot Registry & Audit Stream</h2>
          <p style="font-size:12px; color:#64748b;">Live verified ballots, full Option 1 Agree list, comments & vote journeys</p>
        </div>

        <div style="display:flex; align-items:center; gap:8px; flex-wrap:wrap;">
          <input type="text" id="searchInput" onkeyup="filterCards()" placeholder="Search Flat No. or Name..." class="search-input">
          <button onclick="setFilter('all', this)" class="filter-btn active">All ({len(ballots)})</button>
          <button onclick="setFilter('opt1', this)" class="filter-btn" style="color:#047857; border-color:#a7f3d0; background:#f0fdf4;">✅ Option 1: Agree ({opt1})</button>
          <button onclick="setFilter('opt2', this)" class="filter-btn" style="color:#b45309;">🟡 Suggestions ({opt2})</button>
          <button onclick="setFilter('opt3', this)" class="filter-btn" style="color:#b91c1c;">🔴 Disagree ({opt3})</button>
          <button onclick="setFilter('revised', this)" class="filter-btn" style="color:#7e22ce;">🔄 Revisions (1)</button>
          
          <a href="/export/excel?filter=opt1" download="Mani_Tribhuvan_Option1_Agree_Flats.csv" class="filter-btn" style="background:#ecfdf5; color:#065f46; border-color:#6ee7b7; text-decoration:none;">
            📥 Export Option 1 CSV
          </a>
        </div>
      </div>

      <div id="cardsContainer">
        {cards_html}
      </div>
    </div>

  </div>

  <script>
    function copyWhatsAppSummary() {{
      const summaryText = `*MANI TRIBHUVAN – SETTLEMENT POLL UPDATE*\\n` +
        `🕒 *As of:* {updated}\\n\\n` +
        `📊 *Total Ballots Cast:* {total} / {TOTAL_FLATS} Flats ({turnout_pct} Turnout)\\n` +
        `-----------------------------------------\\n` +
        `✅ *Option 1 (Agree):* {opt1} votes ({pct1})\\n` +
        `🟡 *Option 2 (Suggestions):* {opt2} votes ({pct2})\\n` +
        `🔴 *Option 3 (Disagree):* {opt3} votes ({pct3})\\n` +
        `🔄 *Vote Revisions Recorded:* 1 (Flat 10NA switched to Option 3)`;

      navigator.clipboard.writeText(summaryText).then(() => {{
        const btnText = document.getElementById('copy-btn-text');
        btnText.innerText = '✅ Copied to Clipboard!';
        setTimeout(() => {{ btnText.innerText = '📲 Copy WhatsApp Update'; }}, 2500);
      }}).catch(err => {{
        alert('Could not copy automatically. Please select text manually.');
      }});
    }}

    function setFilter(cat, btn) {{
      document.querySelectorAll('.filter-btn').forEach(b => b.classList.remove('active'));
      if (btn) btn.classList.add('active');

      const items = document.querySelectorAll('.card-item');
      items.forEach(it => {{
        if (cat === 'all' || it.classList.contains(cat)) {{
          it.style.display = 'block';
        }} else {{
          it.style.display = 'none';
        }}
      }});
    }}

    function filterCards() {{
      const q = document.getElementById('searchInput').value.toLowerCase();
      const items = document.querySelectorAll('.card-item');
      items.forEach(it => {{
        const text = it.innerText.toLowerCase();
        it.style.display = text.includes(q) ? 'block' : 'none';
      }});
    }}
  </script>
</body>
</html>
"""

def generate_html(v):
    return f"""
    <html>
      <head>
        <style>
          body {{ font-family: Helvetica, Arial, sans-serif; color: #222; margin: 25px; line-height: 1.5; }}
          h2 {{ color: #0b57d0; border-bottom: 2px solid #0b57d0; padding-bottom: 6px; margin-bottom: 15px; }}
          table {{ width: 100%; border-collapse: collapse; margin-top: 15px; }}
          th, td {{ border: 1px solid #ccc; padding: 10px; text-align: left; font-size: 13px; }}
          th {{ background-color: #f2f4f8; font-weight: bold; width: 35%; }}
          .badge {{ display: inline-block; padding: 4px 8px; border-radius: 4px; background-color: #e6f4ea; color: #137333; font-weight: bold; font-size: 11px; }}
        </style>
      </head>
      <body>
        <h2>MANI TRIBHUVAN – SETTLEMENT POLL RESPONSE RECORD</h2>
        <p><strong>Status:</strong> <span class="badge">{v.get('status', 'LOCKED')}</span> &nbsp;|&nbsp; <strong>Recorded at:</strong> {v.get('timestamp', datetime.datetime.now(IST).strftime('%d/%m/%Y, %I:%M %p'))}</p>
        <table>
          <tr><th>Name</th><td>{v.get('name', 'Society Member')}</td></tr>
          <tr><th>Email Address</th><td>{v.get('email', '')}</td></tr>
          <tr><th>Flat No.</th><td><strong>{v.get('flat_no', '')}</strong></td></tr>
          <tr><th>Response Selection</th><td><strong style="color:#0b57d0;">{v.get('vote', '')}</strong></td></tr>
          <tr><th>Comments / Reasons</th><td>{v.get('comments', 'None')}</td></tr>
          <tr><th>IP Address</th><td>{v.get('ip', 'Not Detected')}</td></tr>
          <tr><th>Device Signature</th><td>{v.get('device', 'Web Browser')}</td></tr>
        </table>
        <br/>
        <p style="font-size: 11px; color: #777;">Official response record generated by Mani Tribhuvan Settlement Management System.</p>
      </body>
    </html>
    """

def send_notifications_for_vote(v):
    flat_no = v.get("flat_no", "")
    name = v.get("name", "")
    member_email = v.get("email", "")

    subject = f"MANI TRIBHUVAN: Settlement Poll Response Recorded (Flat {flat_no} - {name})"
    html_body = generate_html(v)

    try:
        server = smtplib.SMTP("smtp.gmail.com", 587, timeout=30)
        server.ehlo()
        server.starttls()
        server.ehlo()
        server.login(SENDER_EMAIL, APP_PASSWORD)
    except Exception as e:
        print(f"[-] SMTP Connection Error: {e}")
        if "5.4.5" in str(e) or "sending limit" in str(e).lower():
            return "LIMIT_EXCEEDED"
        return False

    success = True
    for to_email in APPOINTED_RECIPIENTS:
        try:
            msg = MIMEMultipart("alternative")
            msg["From"] = f"Mani Tribhuvan Settlement System <{SENDER_EMAIL}>"
            msg["To"] = to_email
            msg["Subject"] = subject
            msg["Date"] = formatdate(localtime=True)
            msg["Message-ID"] = make_msgid(domain="gmail.com")
            msg["Reply-To"] = SENDER_EMAIL
            msg.attach(MIMEText(html_body, "html", "utf-8"))
            server.sendmail(SENDER_EMAIL, [to_email], msg.as_string())
            print(f"[+] Delivered Flat {flat_no} -> {to_email}")
        except Exception as e:
            print(f"[-] Error delivering Flat {flat_no} to {to_email}: {e}")
            if "5.4.5" in str(e) or "sending limit" in str(e).lower():
                try: server.quit()
                except: pass
                return "LIMIT_EXCEEDED"
            success = False

    if member_email and "@" in member_email:
        try:
            msg_v = MIMEMultipart("alternative")
            msg_v["From"] = f"Mani Tribhuvan Residents Welfare Association <{SENDER_EMAIL}>"
            msg_v["To"] = member_email
            msg_v["Subject"] = f"MANI TRIBHUVAN: Settlement Poll Response Recorded (Flat {flat_no})"
            voter_body = f"<p>Dear Member,</p><p>Thank you. Your response on the Draft Settlement Agreement has been recorded for Flat <strong>{flat_no}</strong>.</p><hr/>" + html_body
            msg_v.attach(MIMEText(voter_body, "html", "utf-8"))
            server.sendmail(SENDER_EMAIL, [member_email], msg_v.as_string())
            print(f"[+] Delivered confirmation receipt -> {member_email}")
        except Exception as e:
            print(f"[-] Error delivering receipt to {member_email}: {e}")

    try:
        server.quit()
    except Exception:
        pass

    return success

# Shared state for high-frequency dashboard updates & decoupled email dispatch
members_map_cache = {}
last_sheet_sync_time = 0
sync_lock = threading.Lock()
pending_notifications = []
pending_lock = threading.Lock()

def sync_dashboard_sheet(force=False):
    """Fetches the latest Google Sheet data, busts edge cache, and updates dashboard cache instantly."""
    global last_sheet_sync_time, members_map_cache
    now = time.time()
    if not force and (now - last_sheet_sync_time < 12):
        return

    with sync_lock:
        now = time.time()
        if not force and (now - last_sheet_sync_time < 12):
            return

        if not members_map_cache:
            members_map_cache = load_members_from_cloud()

        try:
            # Cache buster ensures Google edge CDN does not serve stale data
            cache_busted_url = f"{SHEET_EXPORT_URL}&_cb={int(now)}"
            req = urllib.request.Request(cache_busted_url, headers={"User-Agent": "Mozilla/5.0", "Cache-Control": "no-cache"})
            with urllib.request.urlopen(req, timeout=12) as resp:
                content = resp.read().decode("utf-8")
                rows = list(csv.reader(io.StringIO(content)))

            if rows and len(rows) > 1:
                update_dashboard_data(rows, members_map_cache)
                last_sheet_sync_time = now

                # Add new votes to email queue
                with pending_lock:
                    for r in rows[1:]:
                        if len(r) < 4:
                            continue
                        flat_no = r[2].strip().upper()
                        dispatch_status = r[8].strip().upper() if len(r) > 8 else ""

                        if "SENT" in dispatch_status:
                            dispatched.add(flat_no)

                        if flat_no and flat_no not in dispatched:
                            # Avoid duplicates in pending queue
                            if not any(item["flat_no"] == flat_no for item in pending_notifications):
                                member_info = members_map_cache.get(flat_no, {})
                                member_name = member_info.get("name", "Society Member")
                                effective_email = r[1].strip() or member_info.get("email", "")
                                pending_notifications.append({
                                    "name": member_name,
                                    "email": effective_email,
                                    "flat_no": flat_no,
                                    "vote": r[3].strip(),
                                    "comments": r[4].strip() if len(r) > 4 else "None",
                                    "ip": r[5].strip() if len(r) > 5 else "Not Detected",
                                    "device": r[6].strip() if len(r) > 6 else "Web Browser",
                                    "status": r[7].strip() if len(r) > 7 else "LOCKED",
                                    "timestamp": r[0].strip()
                                })
        except Exception as e:
            print(f"[-] Sheet sync error: {e}")

def dashboard_sync_worker():
    """Dedicated background thread for dashboard. Runs every 15s. NEVER blocks on emails."""
    print("=======================================================")
    print("  MANI TRIBHUVAN: DEDICATED REAL-TIME DASHBOARD WORKER")
    print("=======================================================\n")
    while True:
        try:
            sync_dashboard_sheet(force=False)
        except Exception as e:
            print(f"[-] Dashboard sync loop error: {e}")
        time.sleep(15)

def email_dispatch_worker():
    """Isolated email dispatcher. If Gmail SMTP limits trigger, only this worker cools down."""
    print("=======================================================")
    print("  MANI TRIBHUVAN: ISOLATED EMAIL NOTIFICATION WORKER")
    print("=======================================================\n")
    while True:
        vote_obj = None
        with pending_lock:
            while pending_notifications:
                cand = pending_notifications.pop(0)
                if cand["flat_no"] not in dispatched:
                    vote_obj = cand
                    break

        if vote_obj:
            flat_no = vote_obj["flat_no"]
            print(f"[*] NEW VOTE DETECTED: Flat {flat_no} ({vote_obj['name']})! Dispatching email...")
            res = send_notifications_for_vote(vote_obj)
            if res == "LIMIT_EXCEEDED":
                print("[!] Gmail 24-hour limit cooling down. Sleeping for 15 minutes...")
                with pending_lock:
                    pending_notifications.insert(0, vote_obj)
                time.sleep(900)
            elif res:
                dispatched.add(flat_no)
                print(f"[+] Flat {flat_no} completely notified!\n")
            else:
                time.sleep(10)
        else:
            time.sleep(10)

class HealthCheckHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        if self.path in ["/report", "/dashboard", "/report/", "/dashboard/"]:
            # On-demand sync if last sync was >12 seconds ago
            sync_dashboard_sheet(force=False)
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Cache-Control", "no-store, no-cache, must-revalidate, max-age=0")
            self.end_headers()
            html_out = generate_dashboard_html()
            self.wfile.write(html_out.encode("utf-8"))
        elif self.path.startswith("/export/excel") or self.path.startswith("/export/csv"):
            sync_dashboard_sheet(force=False)
            csv_bytes = generate_excel_csv(self.path)
            self.send_response(200)
            self.send_header("Content-Type", "text/csv; charset=utf-8")
            self.send_header("Cache-Control", "no-store, no-cache, must-revalidate, max-age=0")
            filename = "Mani_Tribhuvan_Option1_Agree_Flats.csv" if "opt1" in self.path else "Mani_Tribhuvan_Poll_Registry.csv"
            self.send_header("Content-Disposition", f'attachment; filename="{filename}"')
            self.end_headers()
            self.wfile.write(csv_bytes)
        else:
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Cache-Control", "no-store, no-cache, must-revalidate, max-age=0")
            self.end_headers()
            res = {
                "status": "healthy",
                "service": "Mani Tribhuvan Settlement Poll Cloud Dispatcher",
                "dispatched_count": len(dispatched),
                "total_votes_cached": dashboard_cache.get("total_votes", 0),
                "sender": SENDER_EMAIL,
                "report_url": "/report",
                "export_url": "/export/excel"
            }
            self.wfile.write(json.dumps(res).encode("utf-8"))

    def log_message(self, format, *args):
        return

def start_server():
    port = int(os.environ.get("PORT", 10000))
    server = HTTPServer(("0.0.0.0", port), HealthCheckHandler)
    print(f"[+] Server listening on port {port} (/ and /report)...")
    server.serve_forever()

if __name__ == "__main__":
    t_dash = threading.Thread(target=dashboard_sync_worker, daemon=True)
    t_dash.start()
    t_email = threading.Thread(target=email_dispatch_worker, daemon=True)
    t_email.start()
    start_server()
