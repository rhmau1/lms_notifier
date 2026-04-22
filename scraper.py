import re
import hashlib
import json
import time
from datetime import datetime, timezone
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
                tasks = self._scrape(page, username, password)
                return tasks
            finally:
                browser.close()

    def _scrape(self, page, username: str, password: str) -> list[dict]:
        # ── Step 1: Login SIAKAD ──────────────────────────────────────────
        page.goto(self.SIAKAD_URL, timeout=30000)
        page.wait_for_load_state("networkidle", timeout=30000)

        # Fill login form - SIAKAD uses specific field names
        page.fill('input[name="LoginForm[username]"], input[name="username"], input[type="text"]:visible', username)
        page.fill('input[name="LoginForm[password]"], input[name="password"], input[type="password"]:visible', password)
        page.click('button[type="submit"], input[type="submit"], .btn-login, button:has-text("Login"), button:has-text("Masuk")')
        page.wait_for_load_state("networkidle", timeout=30000)

        # Check login success
        if "beranda" not in page.url and "dashboard" not in page.url:
            # Maybe we're already at beranda, check for error
            error_el = page.query_selector('.alert-danger, .error, #error')
            if error_el:
                raise Exception(f"Login SIAKAD gagal: {error_el.inner_text()}")

        # ── Step 2: Navigate to LMS menu di SIAKAD ───────────────────────
        page.goto("https://siakad.polinema.ac.id/index.php?r=akademik/lms", timeout=30000)
        page.wait_for_load_state("networkidle", timeout=30000)

        # Click "Connect to LMS Polinema" button
        try:
            btn = page.locator('a:has-text("Connect to LMS"), button:has-text("Connect to LMS"), a.btn:has-text("LMS")')
            if btn.count() > 0:
                # This will open a new page or redirect
                with page.expect_navigation(timeout=30000, wait_until="networkidle"):
                    btn.first.click()
        except PlaywrightTimeout:
            pass

        # ── Step 3: Handle intermediate redirects (Spada, etc.) ──────────
        # Wait a bit for any redirects to complete
        time.sleep(2)
        page.wait_for_load_state("networkidle", timeout=20000)

        current_url = page.url
        
        # If landed on Spada, click LMS tab
        if "spada" in current_url.lower() or ("slc.polinema" in current_url and "/spada" in current_url):
            try:
                lms_tab = page.locator('a:has-text("LMS"), .nav-link:has-text("LMS")').first
                with page.expect_navigation(timeout=20000, wait_until="networkidle"):
                    lms_tab.click()
            except:
                pass

        # ── Step 4: Make sure we land on LMS Dashboard ───────────────────
        # If not yet on lmsslc domain, navigate directly
        if "lmsslc.polinema.ac.id" not in page.url:
            page.goto(self.LMS_DASHBOARD, timeout=30000)
            page.wait_for_load_state("networkidle", timeout=30000)
        else:
            # Navigate to /my/ if needed
            page.goto(self.LMS_DASHBOARD, timeout=30000)
            page.wait_for_load_state("networkidle", timeout=30000)

        # Verify we're logged in to LMS
        if "login" in page.url.lower():
            raise Exception("Login LMS gagal! Session tidak terbawa dari SIAKAD.")

        # ── Step 5: Extract sesskey from page ────────────────────────────
        sesskey = self._get_sesskey(page)
        if not sesskey:
            raise Exception("Tidak bisa mendapatkan sesskey LMS. Coba login ulang.")

        # ── Step 6: Call Moodle AJAX API for timeline events ─────────────
        return self._fetch_timeline_via_api(page, sesskey)

    def _get_sesskey(self, page) -> str:
        """Extract Moodle sesskey from page JavaScript config"""
        try:
            sesskey = page.evaluate("() => M.cfg.sesskey")
            if sesskey:
                return sesskey
        except:
            pass

        # Fallback: try from page HTML
        try:
            content = page.content()
            match = re.search(r'"sesskey"\s*:\s*"([^"]+)"', content)
            if match:
                return match.group(1)
            # Try another pattern
            match = re.search(r'sesskey=([A-Za-z0-9]+)', content)
            if match:
                return match.group(1)
        except:
            pass

        return ""

    def _fetch_timeline_via_api(self, page, sesskey: str) -> list[dict]:
        """
        Use Moodle's AJAX API to fetch timeline events.
        This is exactly what the browser does when loading the timeline block.
        Endpoint: /lib/ajax/service.php
        Method: core_calendar_get_action_events_by_timesort
        """
        import time as time_module

        # Time range: 30 days ago to 180 days ahead
        now_ts = int(time_module.time())
        time_from = now_ts - (30 * 24 * 3600)   # 30 days ago (catch overdue)
        time_to = now_ts + (180 * 24 * 3600)     # 180 days ahead

        api_url = f"{self.LMS_BASE}/lib/ajax/service.php?sesskey={sesskey}&info=core_calendar_get_action_events_by_timesort"

        payload = json.dumps([{
            "index": 0,
            "methodname": "core_calendar_get_action_events_by_timesort",
            "args": {
                "limitnum": 50,
                "timesortfrom": time_from,
                "timesortto": time_to,
                "limitfrom": 0,
                "actioneventsinprogress": False
            }
        }])

        # Use fetch via Playwright to make authenticated AJAX call
        result = page.evaluate(f"""
            async () => {{
                const response = await fetch(
                    '{api_url}',
                    {{
                        method: 'POST',
                        headers: {{
                            'Content-Type': 'application/json',
                            'X-Requested-With': 'XMLHttpRequest'
                        }},
                        body: JSON.stringify([{{
                            "index": 0,
                            "methodname": "core_calendar_get_action_events_by_timesort",
                            "args": {{
                                "limitnum": 50,
                                "timesortfrom": {time_from},
                                "timesortto": {time_to},
                                "limitfrom": 0,
                                "actioneventsinprogress": false
                            }}
                        }}])
                    }}
                );
                const data = await response.json();
                return JSON.stringify(data);
            }}
        """)

        if not result:
            raise Exception("API timeline tidak mengembalikan data")

        try:
            data = json.loads(result)
        except Exception as e:
            raise Exception(f"Gagal parse response API: {e}")

        # Parse the response
        return self._parse_api_response(data)

    def _parse_api_response(self, data: list) -> list[dict]:
        """Parse Moodle AJAX API response into task list"""
        tasks = []

        if not data or not isinstance(data, list):
            return tasks

        response = data[0]

        # Check for API errors
        if response.get("error"):
            error_msg = response.get("exception", {}).get("message", "Unknown API error")
            raise Exception(f"Moodle API error: {error_msg}")

        events = response.get("data", {}).get("events", [])

        for event in events:
            try:
                task = self._parse_event(event)
                if task:
                    tasks.append(task)
            except Exception:
                continue

        return tasks

    def _parse_event(self, event: dict) -> dict | None:
        """Parse a single Moodle calendar event into our task format"""
        # Only include assignment/quiz/activity events (not course events)
        event_type = event.get("eventtype", "")
        component = event.get("component", "")

        # Filter: only assignment-type events
        # eventtype: "due" = assignment due date
        # component: mod_assign, mod_quiz, mod_workshop, etc.
        relevant_types = {"due", "gradingdue", "open", "close", "expectcompletionon"}
        relevant_components = {"mod_assign", "mod_quiz", "mod_workshop", "mod_choice",
                               "mod_feedback", "mod_lesson", "mod_scorm", "mod_data",
                               "mod_forum", "mod_glossary"}

        # Include if it's a due-type event from a module
        if event_type not in relevant_types and component not in relevant_components:
            # Still include if it has "due" in the name
            name = event.get("name", "").lower()
            if "due" not in name and "deadline" not in name and "submit" not in name:
                return None

        # Extract fields
        event_id = str(event.get("id", ""))
        name = event.get("name", "").strip()
        
        # Remove " is due" suffix that Moodle adds
        title = re.sub(r'\s+is\s+due\s*$', '', name, flags=re.IGNORECASE).strip()
        if not title:
            title = name

        # Course name
        course_info = event.get("course", {})
        course = course_info.get("fullname", "") if course_info else ""
        # Shorten long course names
        if len(course) > 60:
            course = course[:57] + "..."

        # Deadline timestamp
        timesort = event.get("timesort", 0)
        timestart = event.get("timestart", timesort)
        deadline_ts = timesort or timestart

        deadline_str = "Tidak diketahui"
        if deadline_ts:
            try:
                dt = datetime.fromtimestamp(deadline_ts, tz=timezone.utc)
                # Convert to WIB (UTC+7)
                from datetime import timedelta
                dt_wib = dt + timedelta(hours=7)
                deadline_str = dt_wib.strftime("%d %b %Y %H:%M WIB")
            except:
                deadline_str = str(deadline_ts)

        # URL
        url = event.get("url", "")
        if not url:
            # Build from course module id
            cmid = event.get("instance", "")
            if cmid and component:
                mod = component.replace("mod_", "")
                url = f"{self.LMS_BASE}/mod/{mod}/view.php?id={cmid}"

        # Generate stable ID
        task_id = event_id if event_id else hashlib.md5(f"{title}{course}{deadline_ts}".encode()).hexdigest()[:12]

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

    def _parse_deadline(self, raw: str, attr: str = "") -> str:
        if attr:
            try:
                dt = datetime.fromisoformat(attr.replace("Z", "+00:00"))
                return dt.strftime("%d %b %Y %H:%M")
            except:
                pass
        return raw or "Tidak diketahui"