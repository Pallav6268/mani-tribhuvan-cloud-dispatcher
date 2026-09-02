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

dispatched = {"14LD", "4LA", "6LB", "101", "11CD", "12LB", "1JB", "10NA", "2KC", "14BB", "3JC", "4DC", "13DB", "3BB", "13AA", "9CA", "1KC"}

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
    "comments": [],
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
    hourly = {}
    comments_list = []

    for r in rows[1:]:
        if len(r) < 4:
            continue
        ts_str = r[0].strip()
        email = r[1].strip()
        flat_no = r[2].strip().upper()
        vote = r[3].strip()
        comment = r[4].strip() if len(r) > 4 else ""
        status = r[7].strip() if len(r) > 7 else "LOCKED"

        if "Option 1" in vote:
            opt1 += 1
        elif "Option 2" in vote:
            opt2 += 1
        elif "Option 3" in vote:
            opt3 += 1

        if status.upper() == "LOCKED":
            locked += 1
        else:
            open_to_revise += 1

        try:
            dt = datetime.datetime.strptime(ts_str, "%d/%m/%Y %H:%M:%S")
            hour_key = dt.strftime("%d %b (%I:00 %p)")
        except Exception:
            hour_key = "Other"
        hourly[hour_key] = hourly.get(hour_key, 0) + 1

        if comment and comment.lower() != "none" and comment.strip():
            m_info = members_map.get(flat_no, {})
            m_name = m_info.get("name", "Society Member")
            comments_list.append({
                "flat": flat_no,
                "name": m_name,
                "email": email,
                "time": ts_str,
                "vote": "Option 1: Agree" if "Option 1" in vote else ("Option 2: Suggestions" if "Option 2" in vote else "Option 3: Disagree"),
                "badge_color": "#059669" if "Option 1" in vote else ("#d97706" if "Option 2" in vote else "#dc2626"),
                "badge_bg": "#ecfdf5" if "Option 1" in vote else ("#fffbeb" if "Option 2" in vote else "#fef2f2"),
                "comment": comment
            })

    comments_list.reverse() # Newest first

    with dashboard_lock:
        dashboard_cache["total_votes"] = len(rows) - 1
        dashboard_cache["opt1"] = opt1
        dashboard_cache["opt2"] = opt2
        dashboard_cache["opt3"] = opt3
        dashboard_cache["locked"] = locked
        dashboard_cache["open_to_revise"] = open_to_revise
        dashboard_cache["hourly"] = hourly
        dashboard_cache["comments"] = comments_list
        dashboard_cache["last_updated"] = get_current_ist_time_str()

def generate_dashboard_html():
    with dashboard_lock:
        data = dict(dashboard_cache)

    total = data.get("total_votes", 0)
    opt1 = data.get("opt1", 0)
    opt2 = data.get("opt2", 0)
    opt3 = data.get("opt3", 0)
    locked = data.get("locked", 0)
    open_rev = data.get("open_to_revise", 0)
    hourly = data.get("hourly", {})
    comments = data.get("comments", [])
    updated = data.get("last_updated", get_current_ist_time_str())

    pct1 = f"{(opt1 / total * 100):.1f}%" if total > 0 else "0.0%"
    pct2 = f"{(opt2 / total * 100):.1f}%" if total > 0 else "0.0%"
    pct3 = f"{(opt3 / total * 100):.1f}%" if total > 0 else "0.0%"
    turnout = f"{(total / TOTAL_FLATS * 100):.1f}%"

    hourly_html = ""
    for h_label, h_count in hourly.items():
        hourly_html += f"""
        <div style="background:#f8fafc; border:1px solid #e2e8f0; border-radius:10px; padding:12px; text-align:center; min-width:130px;">
          <div style="font-size:11px; color:#64748b; font-weight:600;">{html.escape(h_label)}</div>
          <div style="font-size:20px; font-weight:800; color:#4f46e5; margin-top:4px;">{h_count} <span style="font-size:12px; font-weight:500; color:#64748b;">vote{'s' if h_count!=1 else ''}</span></div>
        </div>
        """

    comments_html = ""
    if not comments:
        comments_html = "<div style='color:#64748b; font-size:13px; padding:20px; text-align:center;'>No resident comments submitted yet.</div>"
    else:
        for c in comments:
            comments_html += f"""
            <div style="background:#ffffff; border:1px solid #e2e8f0; border-radius:12px; padding:16px; margin-bottom:12px; box-shadow:0 1px 2px rgba(0,0,0,0.03);">
              <div style="display:flex; justify-content:space-between; align-items:center; flex-wrap:wrap; gap:8px; margin-bottom:10px;">
                <div style="display:flex; align-items:center; gap:8px;">
                  <strong style="font-size:15px; color:#0f172a;">Flat {html.escape(c['flat'])}</strong>
                  <span style="font-size:13px; color:#64748b;">• {html.escape(c['name'])}</span>
                </div>
                <div style="display:flex; align-items:center; gap:8px;">
                  <span style="font-size:11px; font-weight:700; padding:3px 8px; border-radius:6px; background:{c['badge_bg']}; color:{c['badge_color']}; border:1px solid {c['badge_color']}30;">
                    {html.escape(c['vote'])}
                  </span>
                  <span style="font-size:11px; color:#94a3b8;">{html.escape(c['time'])}</span>
                </div>
              </div>
              <div style="font-size:13px; color:#334155; line-height:1.6; background:#f8fafc; padding:12px; border-radius:8px; border:1px solid #f1f5f9; white-space:pre-line;">
{html.escape(c['comment'])}
              </div>
            </div>
            """

    return f"""<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  <meta http-equiv="refresh" content="30">
  <title>MANI TRIBHUVAN – Live Poll Dashboard</title>
  <style>
    * {{ box-sizing: border-box; margin: 0; padding: 0; }}
    body {{ font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, Helvetica, Arial, sans-serif; background-color: #f1f5f9; color: #0f172a; padding: 20px; line-height: 1.5; }}
    .container {{ max-width: 1100px; margin: 0 auto; }}
    .card {{ background: #ffffff; border: 1px solid #e2e8f0; border-radius: 16px; padding: 24px; margin-bottom: 20px; box-shadow: 0 1px 3px rgba(0,0,0,0.05); }}
    .header-bar {{ display: flex; justify-content: space-between; align-items: center; flex-wrap: wrap; gap: 12px; }}
    .badge-live {{ display: inline-flex; align-items: center; gap: 6px; padding: 4px 10px; border-radius: 20px; font-size: 11px; font-weight: 700; background: #ecfdf5; color: #059669; border: 1px solid #a7f3d0; }}
    .pulse {{ width: 8px; height: 8px; border-radius: 50%; background: #10b981; }}
    .grid-4 {{ display: grid; grid-template-columns: repeat(auto-fit, minmax(220px, 1fr)); gap: 16px; margin-bottom: 20px; }}
    .metric-card {{ background: #ffffff; border: 1px solid #e2e8f0; border-radius: 12px; padding: 18px; box-shadow: 0 1px 2px rgba(0,0,0,0.04); }}
    .progress-track {{ width: 100%; height: 6px; background: #e2e8f0; border-radius: 3px; margin-top: 10px; overflow: hidden; }}
    .progress-fill {{ height: 100%; border-radius: 3px; }}
    .hourly-scroll {{ display: flex; gap: 12px; overflow-x: auto; padding-bottom: 8px; }}
  </style>
</head>
<body>
  <div class="container">
    
    <!-- Top Header -->
    <div class="card">
      <div class="header-bar">
        <div>
          <div style="margin-bottom:6px;">
            <span class="badge-live"><span class="pulse"></span> LIVE 24/7 CLOUD MONITOR</span>
            <span style="font-size:12px; color:#64748b; margin-left:8px;">Auto-refreshes every 30s</span>
          </div>
          <h1 style="font-size:24px; font-weight:800; color:#0f172a; letter-spacing:-0.5px;">MANI TRIBHUVAN – Settlement Poll Analytics</h1>
          <p style="font-size:13px; color:#64748b; margin-top:2px;">Real-time vote counts, hourly velocity & resident suggestions feed</p>
        </div>
        <div style="text-align:right;">
          <div style="font-size:11px; color:#94a3b8; text-transform:uppercase; font-weight:600;">Last Synced (IST)</div>
          <div style="font-size:14px; font-weight:700; color:#334155;">{updated}</div>
        </div>
      </div>
    </div>

    <!-- 4 KPI Cards -->
    <div class="grid-4">
      <!-- Total Votes -->
      <div class="metric-card">
        <div style="font-size:11px; font-weight:700; color:#64748b; text-transform:uppercase;">Total Votes Recorded</div>
        <div style="display:flex; align-items:baseline; gap:8px; margin-top:4px;">
          <span style="font-size:32px; font-weight:800; color:#4f46e5;">{total}</span>
          <span style="font-size:14px; color:#94a3b8;">/ {TOTAL_FLATS} Flats</span>
        </div>
        <div style="display:inline-block; font-size:11px; font-weight:700; color:#059669; background:#ecfdf5; padding:2px 8px; border-radius:4px; margin-top:6px;">
          {turnout} Turnout
        </div>
      </div>

      <!-- Option 1 -->
      <div class="metric-card" style="border-left: 4px solid #10b981;">
        <div style="font-size:11px; font-weight:700; color:#059669; text-transform:uppercase;">Option 1: Agree</div>
        <div style="display:flex; align-items:baseline; gap:8px; margin-top:4px;">
          <span style="font-size:32px; font-weight:800; color:#0f172a;">{opt1}</span>
          <span style="font-size:14px; font-weight:700; color:#059669;">{pct1}</span>
        </div>
        <div class="progress-track"><div class="progress-fill" style="width:{pct1}; background:#10b981;"></div></div>
      </div>

      <!-- Option 2 -->
      <div class="metric-card" style="border-left: 4px solid #f59e0b;">
        <div style="font-size:11px; font-weight:700; color:#d97706; text-transform:uppercase;">Option 2: Suggestions</div>
        <div style="display:flex; align-items:baseline; gap:8px; margin-top:4px;">
          <span style="font-size:32px; font-weight:800; color:#0f172a;">{opt2}</span>
          <span style="font-size:14px; font-weight:700; color:#d97706;">{pct2}</span>
        </div>
        <div class="progress-track"><div class="progress-fill" style="width:{pct2}; background:#f59e0b;"></div></div>
      </div>

      <!-- Option 3 -->
      <div class="metric-card" style="border-left: 4px solid #ef4444;">
        <div style="font-size:11px; font-weight:700; color:#dc2626; text-transform:uppercase;">Option 3: Disagree</div>
        <div style="display:flex; align-items:baseline; gap:8px; margin-top:4px;">
          <span style="font-size:32px; font-weight:800; color:#0f172a;">{opt3}</span>
          <span style="font-size:14px; font-weight:700; color:#dc2626;">{pct3}</span>
        </div>
        <div class="progress-track"><div class="progress-fill" style="width:{pct3}; background:#ef4444;"></div></div>
      </div>
    </div>

    <!-- Hourly Voting Velocity -->
    <div class="card">
      <div style="display:flex; justify-content:space-between; align-items:center; margin-bottom:14px;">
        <div>
          <h2 style="font-size:16px; font-weight:700; color:#0f172a;">Hourly Voting Activity</h2>
          <p style="font-size:12px; color:#64748b;">Voting volume grouped by 1-hour intervals</p>
        </div>
        <span style="font-size:11px; font-weight:600; color:#475569; background:#f1f5f9; padding:4px 10px; border-radius:6px;">IST Timezone</span>
      </div>
      <div class="hourly-scroll">
        {hourly_html}
      </div>
    </div>

    <!-- Resident Feedback & Suggestions Live Stream -->
    <div class="card">
      <div style="display:flex; justify-content:space-between; align-items:center; margin-bottom:16px;">
        <div>
          <h2 style="font-size:16px; font-weight:700; color:#0f172a;">Resident Feedback & Suggestions Feed</h2>
          <p style="font-size:12px; color:#64748b;">Live feed of specific comments, suggestions (Option 2), and disagreement reasons (Option 3)</p>
        </div>
        <span style="font-size:12px; font-weight:700; color:#b45309; background:#fffbeb; border:1px solid #fef3c7; padding:4px 10px; border-radius:8px;">
          {len(comments)} Comments Submitted
        </span>
      </div>
      <div>
        {comments_html}
      </div>
    </div>

  </div>
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

def background_worker():
    print("=======================================================")
    print("  MANI TRIBHUVAN: CLOUD DISPATCHER DAEMON RUNNING 24/7")
    print(f"  Sender: {SENDER_EMAIL}")
    print("=======================================================\n")

    members_map = load_members_from_cloud()

    while True:
        try:
            req = urllib.request.Request(SHEET_EXPORT_URL, headers={"User-Agent": "Mozilla/5.0"})
            with urllib.request.urlopen(req, timeout=15) as resp:
                content = resp.read().decode("utf-8")
                rows = list(csv.reader(io.StringIO(content)))
                
                if rows:
                    update_dashboard_data(rows, members_map)

                    for r in rows[1:]:
                        if len(r) < 4:
                            continue
                        timestamp = r[0].strip()
                        email = r[1].strip()
                        flat_no = r[2].strip().upper()
                        vote = r[3].strip()
                        comments = r[4].strip() if len(r) > 4 else "None"
                        ip = r[5].strip() if len(r) > 5 else "Not Detected"
                        device = r[6].strip() if len(r) > 6 else "Web Browser"
                        status = r[7].strip() if len(r) > 7 else "LOCKED"

                        if not flat_no:
                            continue

                        if flat_no not in dispatched:
                            member_info = members_map.get(flat_no, {})
                            member_name = member_info.get("name", "Society Member")
                            effective_email = email or member_info.get("email", "")

                            vote_obj = {
                                "name": member_name,
                                "email": effective_email,
                                "flat_no": flat_no,
                                "vote": vote,
                                "comments": comments,
                                "ip": ip,
                                "device": device,
                                "status": status,
                                "timestamp": timestamp
                            }

                            print(f"[*] NEW VOTE DETECTED: Flat {flat_no} ({member_name})! Dispatching...")
                            res = send_notifications_for_vote(vote_obj)
                            if res == "LIMIT_EXCEEDED":
                                print("[!] Gmail 24-hour limit cooling down. Sleeping for 15 minutes...")
                                time.sleep(900)
                                break
                            elif res:
                                dispatched.add(flat_no)
                                print(f"[+] Flat {flat_no} completely notified!\n")
                            else:
                                time.sleep(10)
        except Exception as e:
            print(f"[-] Cloud loop error: {e}")

        time.sleep(20)

class HealthCheckHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        if self.path in ["/report", "/dashboard", "/report/", "/dashboard/"]:
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.end_headers()
            html_out = generate_dashboard_html()
            self.wfile.write(html_out.encode("utf-8"))
        else:
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            res = {
                "status": "healthy",
                "service": "Mani Tribhuvan Settlement Poll Cloud Dispatcher",
                "dispatched_count": len(dispatched),
                "sender": SENDER_EMAIL,
                "report_url": "/report"
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
    t = threading.Thread(target=background_worker, daemon=True)
    t.start()
    start_server()
