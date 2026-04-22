import re
import hashlib
import json
import time
from datetime import datetime, timezone, timedelta
from playwright.sync_api import sync_playwright, TimeoutError as PlaywrightTimeout

 
class LMSScraper:
    SIAKAD_URL = "https://siakad.polinema.ac.id/beranda"
    LMS_BASE = "https://lmsslc.polinema.ac.id"
    LMS_DASHBOARD = "https://lmsslc.polinema.ac.id/my/"

    def get_tasks(self, username: str, password: str) -> list[dict]:
        with sync_playwright() as p:
            browser = p.chromium.launch(
                headless=True,
                args=["--no-sandbox", "--disable-dev-shm-usage", "--disable-gpu"]
            )
            context = browser.new_context(
                user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/120.0.0.0 Safari/537.36"
            )
            page = context.new_page()
            try:
                return self._scrape(page, username, password)
            finally:
                browser.close()

    def _scrape(self, page, username: str, password: str) -> list[dict]:
        # ── Step 1: Load SIAKAD login page ───────────────────────────────
        page.goto(self.SIAKAD_URL, timeout=30000)
        page.wait_for_load_state("networkidle", timeout=30000)

        # ── Step 2: Submit login form ─────────────────────────────────────
        page.fill('input[name="LoginForm[username]"], input[name="username"]', username)
        page.fill('input[name="LoginForm[password]"], input[name="password"]', password)
        page.click('button[type="submit"], input[type="submit"]')

        # ── Step 3: Wait for post-login redirect ──────────────────────────
        deadline = time.time() + 60
        while time.time() < deadline:
            time.sleep(2)
            try:
                page.wait_for_load_state("networkidle", timeout=8000)
            except PlaywrightTimeout:
                pass

            url = page.url
            if any(x in url for x in ["beranda", "dashboard", "lmsslc", "spada"]):
                break

            body = ""
            try:
                body = page.inner_text("body").lower()
            except Exception:
                pass

            sync_phrases = ["sinkronisasi", "sedang login", "mohon tunggu", "data anda valid", "proses penyimpanan"]
            is_loading_screen = any(p in body for p in sync_phrases)

            if not is_loading_screen:
                err_el = page.query_selector('.alert-danger, #error-summary, .help-block.error')
                if err_el:
                    err_text = err_el.inner_text().strip()
                    if not any(p in err_text.lower() for p in ["valid", "berhasil", "sukses"]):
                        raise Exception(f"Login SIAKAD gagal: {err_text[:120]}")
        else:
            raise Exception("Login SIAKAD timeout: redirect tidak selesai dalam 60 detik.")

        # ── Step 4: Navigate to Akademik → LMS page ──────────────────────
        page.goto("https://siakad.polinema.ac.id/index.php?r=akademik/lms", timeout=30000)
        page.wait_for_load_state("networkidle", timeout=30000)

        # ── Step 5: Click "Connect to LMS Polinema" ───────────────────────
        try:
            btn = page.locator('a:has-text("Connect to LMS"), button:has-text("Connect to LMS"), a.btn:has-text("LMS Polinema")')
            if btn.count() > 0:
                with page.expect_navigation(timeout=30000, wait_until="networkidle"):
                    btn.first.click()
        except PlaywrightTimeout:
            pass

        # ── Step 6: Handle Spada intermediate ─────────────────────────────
        time.sleep(2)
        if "spada" in page.url.lower() and "lmsslc" not in page.url:
            try:
                lms_tab = page.locator('a:has-text("LMS"), .nav-link:has-text("LMS")').first
                with page.expect_navigation(timeout=20000, wait_until="networkidle"):
                    lms_tab.click()
            except Exception:
                pass

        # ── Step 7: Navigate directly to LMS dashboard ────────────────────
        page.goto(self.LMS_DASHBOARD, timeout=30000)
        page.wait_for_load_state("networkidle", timeout=30000)

        if "login" in page.url.lower() and "lmsslc" in page.url:
            raise Exception("Gagal masuk LMS: sesi tidak terbawa dari SIAKAD.")

        # ── Step 8: Get auth data (sesskey & userid) ──────────────────────
        auth = self._get_auth_data(page)
        if not auth["sesskey"]:
            raise Exception("Gagal mendapatkan sesskey LMS.")

        # ── Step 9: Fetch events via Moodle AJAX API ──────────────────────
        return self._fetch_tasks_via_api(page, auth)

    def _get_auth_data(self, page) -> dict:
        auth = {"sesskey": "", "userid": 0}
        try:
            # 1. Try to get from Moodle config
            cfg = page.evaluate("() => typeof M !== 'undefined' ? M.cfg : {}")
            auth["sesskey"] = cfg.get("sesskey", "")
            auth["userid"] = int(cfg.get("userid", 0))
            
            # Note: userid=1 is usually 'Guest'. We want the real student ID (> 1).
            if auth["userid"] <= 1:
                auth["userid"] = 0
        except Exception:
            pass

        # 2. Extract sesskey from HTML if missing
        if not auth["sesskey"]:
            try:
                content = page.content()
                m = re.search(r'"sesskey"\s*:\s*"([A-Za-z0-9]+)"', content)
                if m: auth["sesskey"] = m.group(1)
            except Exception:
                pass

        # 3. Robust Fallback for UserID: look for data-userid attributes in the DOM
        if auth["userid"] <= 1:
            try:
                # Common locations for student ID: timeline block, user menu, etc.
                uid = page.evaluate("""() => {
                    const el = document.querySelector('[data-userid]:not([data-userid="1"]):not([data-userid="0"])');
                    if (el) return el.dataset.userid;
                    const profileLink = document.querySelector('a[href*="/user/profile.php?id="]');
                    if (profileLink) {
                        const m = profileLink.href.match(/id=(\d+)/);
                        return m ? m[1] : null;
                    }
                    return null;
                }""")
                if uid: 
                    auth["userid"] = int(uid)
            except Exception:
                pass

        print(f"DIAGNOSTIC: Page Title='{page.title()}', URL='{page.url}'")
        print(f"DIAGNOSTIC: Extraction Result -> sesskey:{'OK' if auth['sesskey'] else 'MISSING'}, userid:{auth['userid']}")
        return auth

    def _fetch_tasks_via_api(self, page, auth: dict) -> list[dict]:
        sesskey = auth["sesskey"]
        userid = auth["userid"]
        now_ts = int(time.time())
        
        # Range: 90 days ago to 6 months ahead
        time_from = int(now_ts - (90 * 24 * 3600))
        time_to = int(now_ts + (180 * 24 * 3600))

        api_url = f"{self.LMS_BASE}/lib/ajax/service.php?sesskey={sesskey}"

        # Fetch from BOTH Timeline and Upcoming View to be safe
        result = page.evaluate(f"""
            async () => {{
                try {{
                    const resp = await fetch('{api_url}', {{
                        method: 'POST',
                        headers: {{ 
                            'Content-Type': 'application/json', 
                            'X-Requested-With': 'XMLHttpRequest' 
                        }},
                        body: JSON.stringify([
                            {{
                                index: 0,
                                methodname: 'core_calendar_get_action_events_by_timesort',
                                args: {{
                                    limitnum: 100,
                                    timesortfrom: {time_from},
                                    timesortto: {time_to},
                                    aftereventid: 0,
                                    userid: {userid}
                                }}
                            }},
                            {{
                                index: 1,
                                methodname: 'core_calendar_get_calendar_upcoming_view',
                                args: {{ courseid: 0, categoryid: 0, userid: {userid} }}
                            }}
                        ])
                    }});
                    const json = await resp.json();
                    return JSON.stringify(json);
                }} catch (e) {{
                    return JSON.stringify({{ fetch_error: e.toString() }});
                }}
            }}
        """)

        if not result:
            raise Exception("API LMS tidak mengembalikan data.")

        data = json.loads(result)
        if isinstance(data, dict) and data.get("fetch_error"):
            raise Exception(f"Fetch API error: {data['fetch_error']}")
        
        print(f"DIAGNOSTIC: API Response status: {'List' if isinstance(data, list) else type(data)}")
        if isinstance(data, list):
            for i, res in enumerate(data):
                err = res.get("error", False)
                print(f"DIAGNOSTIC: Method[{i}] result: {'ERROR' if err else 'SUCCESS'}")
                if err: print(f"DIAGNOSTIC: Method[{i}] error details: {res.get('exception')}")

        return self._parse_multi_api_response(data)

    def _parse_multi_api_response(self, data: list) -> list[dict]:
        if not data or not isinstance(data, list):
            return []

        all_events = []
        for response in data:
            if response.get("error"):
                continue 
            
            res_data = response.get("data", {})
            events = res_data.get("events", [])
            idx = response.get("index", "?")
            print(f"DIAGNOSTIC: Method[{idx}] returned {len(events) if isinstance(events, list) else 0} raw events")
            if isinstance(events, list):
                all_events.extend(events)

        unique_tasks = {}
        for event in all_events:
            try:
                task = self._parse_event(event)
                if task and task["id"] not in unique_tasks:
                    unique_tasks[task["id"]] = task
            except Exception:
                continue

        return sorted(unique_tasks.values(), key=lambda x: x["deadline_ts"])

    def _parse_event(self, event: dict) -> dict | None:
        event_type = event.get("eventtype", "")
        component = event.get("component", "")

        activity_components = {"mod_assign", "mod_quiz", "mod_workshop", "mod_choice", "mod_feedback", "mod_lesson", "mod_scorm", "mod_data", "mod_forum", "mod_glossary", "mod_h5pactivity"}
        activity_event_types = {"due", "gradingdue", "open", "close", "expectcompletionon"}

        is_activity = (component in activity_components or event_type in activity_event_types)
        
        if not is_activity:
            name_lc = event.get("name", "").lower()
            interesting_keywords = ["due", "deadline", "submit", "laporan", "tugas", "milestone", "quiz", "ujian", "uas", "uts"]
            found_kw = [k for k in interesting_keywords if k in name_lc]
            if not found_kw:
                # Filter out generic/meta events
                if event_type in ["site", "category", "user"]:
                    print(f"DIAGNOSTIC: Filtered out generic event: {event.get('name')} (type: {event_type})")
                    return None
            else:
                print(f"DIAGNOSTIC: Keeping keyword-matched event: {event.get('name')}")

        raw_name = event.get("name", "").strip()
        title = re.sub(r'\s+is\s+due\s*$', '', raw_name, flags=re.IGNORECASE).strip() or raw_name

        course_info = event.get("course") or {}
        course = course_info.get("fullname", "")
        if len(course) > 70: course = course[:67] + "..."

        deadline_ts = event.get("timesort") or event.get("timestart") or 0
        deadline_str = "Tidak diketahui"
        if deadline_ts:
            try:
                dt_wib = datetime.fromtimestamp(deadline_ts, tz=timezone.utc) + timedelta(hours=7)
                deadline_str = dt_wib.strftime("%d %b %Y %H:%M WIB")
            except Exception:
                deadline_str = str(deadline_ts)

        url = event.get("url", "")
        if not url:
            instance = event.get("instance", "")
            mod = component.replace("mod_", "") if component.startswith("mod_") else ""
            if instance and mod:
                url = f"{self.LMS_BASE}/mod/{mod}/view.php?id={instance}"

        task_id = str(event.get("id", "")) or hashlib.md5(f"{title}{course}{deadline_ts}".encode()).hexdigest()[:12]

        return {
            "id": task_id,
            "title": title,
            "course": course,
            "deadline": deadline_str,
            "deadline_ts": deadline_ts,
            "link": url,
            "event_type": event_type,
            "component": component,
            "scraped_at": datetime.now().isoformat(),
        }