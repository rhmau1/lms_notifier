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

        # ── Step 8: Get sesskey ───────────────────────────────────────────
        sesskey = self._get_sesskey(page)
        if not sesskey:
            raise Exception("Gagal mendapatkan sesskey LMS.")

        # ── Step 9: Fetch events via Moodle AJAX API ──────────────────────
        return self._fetch_timeline_via_api(page, sesskey)

    def _get_sesskey(self, page) -> str:
        try:
            key = page.evaluate("() => typeof M !== 'undefined' && M.cfg && M.cfg.sesskey ? M.cfg.sesskey : null")
            if key: return key
        except Exception: pass
        content = page.content()
        m = re.search(r'"sesskey"\s*:\s*"([A-Za-z0-9]+)"', content)
        return m.group(1) if m else ""

    def _fetch_timeline_via_api(self, page, sesskey: str) -> list[dict]:
        now_ts = int(time.time())
        # Moodle API sangat sensitif terhadap tipe data (harus Integer)
        time_from = int(now_ts - (30 * 24 * 3600))
        time_to = int(now_ts + (180 * 24 * 3600))

        api_url = f"{self.LMS_BASE}/lib/ajax/service.php?sesskey={sesskey}&info=core_calendar_get_action_events_by_timesort"

        # Perbaikan: Memastikan payload dikirim sebagai array objek dengan tipe data integer yang benar
        result = page.evaluate(f"""
            async () => {{
                try {{
                    const response = await fetch('{api_url}', {{
                        method: 'POST',
                        headers: {{
                            'Content-Type': 'application/json',
                            'X-Requested-With': 'XMLHttpRequest'
                        }},
                        body: JSON.stringify([{{
                            index: 0,
                            methodname: 'core_calendar_get_action_events_by_timesort',
                            args: {{
                                limitnum: 50,
                                timesortfrom: Number({time_from}),
                                timesortto: Number({time_to}),
                                aftereventid: 0
                            }}
                        }}])
                    }});
                    const json = await response.json();
                    return JSON.stringify(json);
                }} catch (e) {{
                    return JSON.stringify({{ fetch_error: e.toString() }});
                }}
            }}
        """)

        if not result:
            raise Exception("API timeline tidak mengembalikan data.")

        data = json.loads(result)
        if isinstance(data, dict) and data.get("fetch_error"):
            raise Exception(f"Fetch API error: {data['fetch_error']}")

        return self._parse_api_response(data)

    def _parse_api_response(self, data: list) -> list[dict]:
        if not data or not isinstance(data, list):
            return []

        response = data[0]
        if response.get("error"):
            msg = response.get("exception", {}).get("message", "Unknown API error")
            # Jika error masih "Invalid parameter", kita log detailnya
            raise Exception(f"Moodle API error: {msg}")

        events = response.get("data", {}).get("events", [])
        tasks = []
        for event in events:
            try:
                parsed = self._parse_event(event)
                if parsed: tasks.append(parsed)
            except Exception: continue
        return tasks

    def _parse_event(self, event: dict) -> dict | None:
        event_type = event.get("eventtype", "")
        component = event.get("component", "")

        activity_components = {"mod_assign", "mod_quiz", "mod_workshop", "mod_choice", "mod_feedback", "mod_lesson", "mod_scorm", "mod_data", "mod_forum", "mod_glossary", "mod_h5pactivity"}
        activity_event_types = {"due", "gradingdue", "open", "close", "expectcompletionon"}

        is_activity = (component in activity_components or event_type in activity_event_types)
        if not is_activity:
            name_lc = event.get("name", "").lower()
            if not any(k in name_lc for k in ["due", "deadline", "submit", "laporan", "tugas", "milestone"]):
                return None

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