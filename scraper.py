import re
import hashlib
from datetime import datetime
from playwright.sync_api import sync_playwright, TimeoutError as PlaywrightTimeout


class LMSScraper:
    SIAKAD_URL = "https://siakad.polinema.ac.id/beranda"
    LMS_DASHBOARD = "https://lmsslc.polinema.ac.id/my/"

    def get_tasks(self, username: str, password: str) -> list[dict]:
        with sync_playwright() as p:
            browser = p.chromium.launch(
                headless=True,
                args=["--no-sandbox", "--disable-dev-shm-usage"]
            )
            context = browser.new_context(
                user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"
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
        # Fill login form
        page.fill('input[name="username"], input[type="text"]', username)
        page.fill('input[name="password"], input[type="password"]', password)
        page.click('button[type="submit"], input[type="submit"]')
        page.wait_for_load_state("networkidle", timeout=30000)

        # ── Step 2: Navigate to LMS menu ─────────────────────────────────
        # Go to LMS connector page
        try:
            # Naikkan timeout menjadi 60 detik (60000)
            page.goto("https://siakad.polinema.ac.id/index.php?r=akademik/lms", 
                      timeout=60000, 
                      wait_until="domcontentloaded") # Lebih ringan daripada networkidle
        except Exception as e:
            add_log(f"Gagal memuat menu LMS: {str(e)}", "error")

        # Click "Connect to LMS Polinema" button
        try:
            btn = page.locator('a:has-text("Connect to LMS"), button:has-text("Connect to LMS")')
            if btn.count() > 0:
                with page.expect_navigation(timeout=30000):
                    btn.first.click()
                page.wait_for_load_state("networkidle", timeout=30000)
        except PlaywrightTimeout:
            pass

        # ── Step 3: Handle Spada → LMS redirect ──────────────────────────
        # May redirect through slc.polinema.ac.id/spada/ first
        current = page.url
        if "spada" in current or "slc.polinema" in current:
            # Click LMS tab if on Spada
            try:
                lms_tab = page.locator('text=LMS').first
                lms_tab.click()
                page.wait_for_load_state("networkidle", timeout=15000)
            except:
                pass

        # ── Step 4: Navigate to LMS Dashboard ────────────────────────────
        page.goto(self.LMS_DASHBOARD, timeout=30000)
        page.wait_for_load_state("networkidle", timeout=30000)

        # Verify we're logged in
        if "login" in page.url.lower():
            raise Exception("Login gagal! Cek username/password SIAKAD kamu.")

        try:
            # 1. Cari tombol filter (berdasarkan teks atau icon clock)
            filter_btn = page.locator('[data-region="day-filter"] button')
            if filter_btn.is_visible():
                filter_btn.click()
                # 2. Klik opsi "All" pada dropdown
                # Berdasarkan HTML kamu: data-filtername="all"
                page.locator('[data-region="day-filter"] [data-filtername="all"]').click()
        
                # 3. Tunggu sebentar agar list group ter-update (AJAX load)
                page.wait_for_load_state("networkidle", timeout=10000)
                add_log("📅 Filter 'All' diaktifkan", "info")
        except Exception as e:
            add_log(f"⚠️ Gagal set filter All: {str(e)}", "warn")
        # ── Step 5: Scrape Timeline tasks ────────────────────────────────
        page.screenshot(path="debug.png")
        return self._parse_timeline(page)

    def _parse_timeline(self, page) -> list[dict]:
        tasks = []

        # Wait for timeline to load
        try:
            page.wait_for_selector('[data-region="event-list-item"], .timeline-event, [data-region="paged-content"]', timeout=15000)
        except PlaywrightTimeout:
            pass

        # Extract timeline items
        # Try multiple selectors for LMS Moodle timeline
        selectors = [
            '[data-region="event-list-item"]', # Ini yang paling cocok dengan HTML kamu
            '.list-group-item.flex-column',
            '.event-name-container'
        ]

        items = []
        for sel in selectors:
            found = page.query_selector_all(sel)
            if found:
                items = found
                break

        if not items:
            # Fallback: parse raw HTML
            return self._parse_html_fallback(page)

        for item in items:
            try:
                task = self._extract_task(item)
                if task:
                    tasks.append(task)
            except Exception:
                continue

        # If still empty, try fallback
        if not tasks:
            return self._parse_html_fallback(page)

        return tasks

    def _extract_task(self, element) -> dict | None:
        # 1. Ambil Judul dan Link
        # Di HTML kamu: <h6 class="event-name">"Judul Tugas"</h6>
        title_el = element.query_selector(".event-name")
        link_el = element.query_selector("a[title]")
        
        if not title_el: return None
    
        title = title_el.inner_text().strip().strip('"')
        link = link_el.get_attribute("href") if link_el else ""

        # 2. Ambil Mata Kuliah
        # Di HTML kamu: <small class="text-muted">"Nama Matkul"</small>
        course_el = element.query_selector("small.text-muted")
        course = course_el.inner_text().strip().strip('"') if course_el else ""

        # 3. Ambil Jam Deadline
        # Di HTML kamu: <small class="text-right"> 18:00 </small>
        time_el = element.query_selector("small.text-right")
        time_str = time_el.inner_text().strip() if time_el else ""

        # 4. Ambil Tanggal (Cari heading h5 terdekat di atasnya)
        # Ini agak tricky di Playwright, tapi bisa menggunakan evaluate
        date_str = element.evaluate("""el => {
            let header = el.closest('.list-group').previousElementSibling;
            return header ? header.innerText : '';
        }""")

        full_deadline = f"{date_str} {time_str}".strip()

        task_id = hashlib.md5(f"{title}{course}".encode()).hexdigest()[:12]

        return {
            "id": task_id,
            "title": title,
            "course": course,
            "deadline": full_deadline,
            "link": link,
            "scraped_at": datetime.now().isoformat(),
        }

    def _parse_html_fallback(self, page) -> list[dict]:
        """Fallback: parse the full page content for task mentions"""
        tasks = []
        content = page.content()

        # Look for "is due" patterns in the page
        # Moodle timeline shows tasks as "[Task name] is due"
        import re
        from bs4 import BeautifulSoup

        soup = BeautifulSoup(content, "html.parser")

        # Find date headers and their following tasks
        # Moodle groups tasks under date headings
        date_headers = soup.find_all(["h5", "h4", "h3", "p"], string=re.compile(
            r'(January|February|March|April|May|June|July|August|September|October|November|December|'
            r'Januari|Februari|Maret|April|Mei|Juni|Juli|Agustus|September|Oktober|November|Desember)'
        ))

        current_date = ""
        for header in date_headers:
            current_date = header.get_text(strip=True)
            # Find tasks in the next sibling elements
            sibling = header.find_next_sibling()
            while sibling and sibling.name not in ["h5", "h4", "h3"]:
                links = sibling.find_all("a")
                for link in links:
                    title = link.get_text(strip=True)
                    if "is due" in title or title:
                        task_id = hashlib.md5(f"{title}{current_date}".encode()).hexdigest()[:12]
                        # Find course name nearby
                        parent = link.find_parent()
                        small = parent.find("small") if parent else None
                        course = small.get_text(strip=True) if small else ""

                        tasks.append({
                            "id": task_id,
                            "title": title.replace(" is due", ""),
                            "course": course,
                            "deadline": current_date,
                            "deadline_raw": current_date,
                            "link": link.get("href", ""),
                            "scraped_at": datetime.now().isoformat(),
                        })
                sibling = sibling.find_next_sibling() if sibling else None

        return tasks

    def _parse_deadline(self, raw: str, attr: str = "") -> str:
        if attr:
            try:
                dt = datetime.fromisoformat(attr.replace("Z", "+00:00"))
                return dt.strftime("%d %b %Y %H:%M")
            except:
                pass
        return raw or "Tidak diketahui"
