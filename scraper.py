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

        # ── Step 3: Wait for post-login redirect to fully complete ─────────
        # SIAKAD shows "Sedang login. Mohon tunggu, sistem sedang melakukan
        # sinkronisasi data anda" — this is a NORMAL loading screen after
        # SUCCESSFUL login. It is NOT an error. We must wait until we land
        # on a final page (beranda/dashboard), not treat this text as failure.
        #
        # Strategy: poll the current URL every 2 seconds until we leave the
        # login/sync area, up to 60 seconds total.

        deadline = time.time() + 60  # max 60s for the whole redirect chain
        while time.time() < deadline:
            time.sleep(2)
            try:
                page.wait_for_load_state("networkidle", timeout=8000)
            except PlaywrightTimeout:
                pass

            url = page.url
            # Success conditions: we left the login page
            if (
                "beranda" in url
                or "dashboard" in url
                or "lmsslc" in url
                or "spada" in url
            ):
                break

            # Check for a REAL wrong-password error (not the sync loading msg)
            body = ""
            try:
                body = page.inner_text("body")
            except Exception:
                pass

            sync_phrases = ["sinkronisasi", "sedang login", "mohon tunggu"]
            is_loading_screen = any(p in body.lower() for p in sync_phrases)

            if not is_loading_screen:
                # We're not loading — check if we're still on login with an error
                err_el = page.query_selector('.alert-danger, #error-summary, .help-block.error')
                if err_el:
                    err_text = err_el.inner_text().strip()
                    raise Exception(f"Login SIAKAD gagal (password salah?): {err_text[:120]}")
                # No error element — just keep waiting
        else:
            # Timed out still on login
            raise Exception("Login SIAKAD timeout: redirect tidak selesai dalam 60 detik.")

        # ── Step 4: Navigate to Akademik → LMS page ──────────────────────
        page.goto(
            "https://siakad.polinema.ac.id/index.php?r=akademik/lms",
            timeout=30000
        )
        page.wait_for_load_state("networkidle", timeout=30000)

        # ── Step 5: Click "Connect to LMS Polinema" ───────────────────────
        try:
            btn = page.locator(
                'a:has-text("Connect to LMS"), '
                'button:has-text("Connect to LMS"), '
                'a.btn:has-text("LMS Polinema")'
            )
            if btn.count() > 0:
                with page.expect_navigation(timeout=30000, wait_until="networkidle"):
                    btn.first.click()
        except PlaywrightTimeout:
            pass

        # ── Step 6: Handle Spada intermediate page ─────────────────────
        time.sleep(2)
        try:
            page.wait_for_load_state("networkidle", timeout=15000)
        except PlaywrightTimeout:
            pass

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

        # Verify we're actually logged in to LMS
        if "login" in page.url.lower() and "lmsslc" in page.url:
            raise Exception("Gagal masuk LMS: sesi tidak terbawa dari SIAKAD.")

        # ── Step 8: Get sesskey ───────────────────────────────────────────
        sesskey = self._get_sesskey(page)
        if not sesskey:
            raise Exception("Gagal mendapatkan sesskey LMS.")

        # ── Step 9: Fetch events via Moodle AJAX API ──────────────────────
        return self._fetch_timeline_via_api(page, sesskey)

    # ─────────────────────────────────────────────────────────────────────

    def _get_sesskey(self, page) -> str:
        try:
            key = page.evaluate("() => M.cfg && M.cfg.sesskey ? M.cfg.sesskey : null")
            if key:
                return key
        except Exception:
            pass
        try:
            content = page.content()
            m = re.search(r'"sesskey"\s*:\s*"([A-Za-z0-9]+)"', content)
            if m:
                return m.group(1)
        except Exception:
            pass
        return ""

    def _fetch_timeline_via_api(self, page, sesskey: str) -> list[dict]:
        now_ts = int(time.time())
        time_from = now_ts - (30 * 24 * 3600)   # 30 days ago (include overdue)
        time_to   = now_ts + (180 * 24 * 3600)  # 6 months ahead

        api_url = (
            f"{self.LMS_BASE}/lib/ajax/service.php"
            f"?sesskey={sesskey}"
            f"&info=core_calendar_get_action_events_by_timesort"
        )

        result = page.evaluate(f"""
            async () => {{
                try {{
                    const resp = await fetch('{api_url}', {{
                        method: 'POST',
                        headers: {{
                            'Content-Type': 'application/json',
                            'X-Requested-With': 'XMLHttpRequest'
                        }},
                        body: JSON.stringify([{{
                            index: 0,
                            methodname: 'core_calendar_get_action_events_by_timesort',
                            args: {{
                                limitnum: 100,
                                timesortfrom: {time_from},
                                timesortto: {time_to},
                                limitfrom: 0,
                                actioneventsinprogress: false
                            }}
                        }}])
                    }});
                    const data = await resp.json();
                    return JSON.stringify(data);
                }} catch(e) {{
                    return JSON.stringify({{fetch_error: e.toString()}});
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
            raise Exception(f"Moodle API error: {msg}")

        events = response.get("data", {}).get("events", [])
        tasks = []
        for event in events:
            try:
                t = self._parse_event(event)
                if t:
                    tasks.append(t)
            except Exception:
                continue
        return tasks

    def _parse_event(self, event: dict) -> dict | None:
        event_type = event.get("eventtype", "")
        component  = event.get("component", "")

        # Keep only activity-related events (assignments, quizzes, etc.)
        activity_components = {
            "mod_assign", "mod_quiz", "mod_workshop", "mod_choice",
            "mod_feedback", "mod_lesson", "mod_scorm", "mod_data",
            "mod_forum", "mod_glossary", "mod_h5pactivity",
        }
        activity_event_types = {"due", "gradingdue", "open", "close", "expectcompletionon"}

        is_activity = (
            component in activity_components
            or event_type in activity_event_types
        )
        if not is_activity:
            # Still include if name suggests a deadline
            name_lc = event.get("name", "").lower()
            if not any(k in name_lc for k in ["due", "deadline", "submit", "laporan", "tugas", "milestone"]):
                return None

        # Title — strip " is due" suffix Moodle appends
        raw_name = event.get("name", "").strip()
        title = re.sub(r'\s+is\s+due\s*$', '', raw_name, flags=re.IGNORECASE).strip() or raw_name

        # Course
        course_info = event.get("course") or {}
        course = course_info.get("fullname", "")
        if len(course) > 70:
            course = course[:67] + "..."

        # Deadline timestamp
        deadline_ts = event.get("timesort") or event.get("timestart") or 0

        deadline_str = "Tidak diketahui"
        if deadline_ts:
            try:
                dt_utc = datetime.fromtimestamp(deadline_ts, tz=timezone.utc)
                dt_wib = dt_utc + timedelta(hours=7)
                deadline_str = dt_wib.strftime("%d %b %Y %H:%M WIB")
            except Exception:
                deadline_str = str(deadline_ts)

        # URL
        url = event.get("url", "")
        if not url:
            instance = event.get("instance", "")
            mod = component.replace("mod_", "") if component.startswith("mod_") else ""
            if instance and mod:
                url = f"{self.LMS_BASE}/mod/{mod}/view.php?id={instance}"

        task_id = str(event.get("id", "")) or hashlib.md5(
            f"{title}{course}{deadline_ts}".encode()
        ).hexdigest()[:12]

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