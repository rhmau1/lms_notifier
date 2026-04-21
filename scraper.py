import logging
import re
from datetime import datetime
from playwright.sync_api import sync_playwright, TimeoutError as PlaywrightTimeout
from bs4 import BeautifulSoup

logger = logging.getLogger(__name__)

LMS_URL = "https://lmsslc.polinema.ac.id"
LOGIN_URL = "https://siakad.polinema.ac.id"


class LMSScraper:
    def __init__(self):
        self.tasks = []

    def get_tasks(self, username: str, password: str) -> list[dict]:
        with sync_playwright() as p:
            browser = p.chromium.launch(
                headless=True,
                args=[
                    "--no-sandbox",
                    "--disable-setuid-sandbox",
                    "--disable-dev-shm-usage",
                    "--disable-gpu",
                ]
            )
            context = browser.new_context(
                viewport={"width": 1280, "height": 800},
                user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
            )
            page = context.new_page()

            try:
                tasks = self._scrape(page, username, password)
                return tasks
            except Exception as e:
                logger.error(f"Scraping error: {e}")
                raise
            finally:
                browser.close()

    def _scrape(self, page, username: str, password: str) -> list[dict]:
        # ── 1. Login via SIAKAD ──────────────────────────────────────────────
        logger.info("Navigating to LMS login page...")
        page.goto(f"{LMS_URL}/login/index.php", timeout=30000)
        page.wait_for_load_state("networkidle", timeout=15000)

        # Klik tombol login via SIAKAD jika ada
        try:
            siakad_btn = page.locator("a:has-text('SIAKAD'), a[href*='siakad'], button:has-text('SIAKAD')")
            if siakad_btn.count() > 0:
                logger.info("Clicking SIAKAD login button...")
                siakad_btn.first.click()
                page.wait_for_load_state("networkidle", timeout=15000)
        except Exception:
            logger.info("No SIAKAD button found, trying direct login...")

        # Isi form login
        current_url = page.url
        logger.info(f"Current URL after redirect: {current_url}")

        try:
            # Coba form login SIAKAD style
            if "siakad" in current_url or "sso" in current_url or "cas" in current_url:
                logger.info("Filling SIAKAD login form...")
                page.fill("input[name='username'], input[type='text']", username)
                page.fill("input[name='password'], input[type='password']", password)
                page.click("input[type='submit'], button[type='submit']")
            else:
                # Form login Moodle biasa
                logger.info("Filling Moodle login form...")
                page.fill("#username", username)
                page.fill("#password", password)
                page.click("#loginbtn")

            page.wait_for_load_state("networkidle", timeout=20000)
        except PlaywrightTimeout:
            logger.warning("Timeout after login, continuing...")

        # Handle kemungkinan redirect chain SSO
        for _ in range(3):
            current_url = page.url
            logger.info(f"URL after login attempt: {current_url}")
            if LMS_URL in current_url:
                break
            # Jika masih di SSO dan ada form lagi
            try:
                submit = page.locator("input[type='submit'], button[type='submit']")
                if submit.count() > 0:
                    submit.first.click()
                    page.wait_for_load_state("networkidle", timeout=15000)
            except Exception:
                break

        # ── 2. Verifikasi sudah login ────────────────────────────────────────
        if LMS_URL not in page.url:
            logger.info(f"Navigating to LMS dashboard... current url: {page.url}")
            page.goto(f"{LMS_URL}/my/", timeout=30000)
            page.wait_for_load_state("networkidle", timeout=15000)

        logger.info(f"Final URL: {page.url}")

        # Cek apakah berhasil login (ada elemen user menu atau dashboard)
        try:
            page.wait_for_selector(
                ".usermenu, #user-menu, .navbar-nav, [data-region='timeline']",
                timeout=10000
            )
        except PlaywrightTimeout:
            raise Exception("Login gagal atau halaman tidak dimuat dengan benar")

        # ── 3. Tunggu timeline block muncul ──────────────────────────────────
        logger.info("Waiting for timeline block...")
        try:
            page.wait_for_selector(
                "[data-region='event-list-content']",
                timeout=20000
            )
            # Tunggu sebentar agar JS selesai render
            page.wait_for_timeout(2000)
        except PlaywrightTimeout:
            logger.warning("Timeline block not found, trying to navigate to dashboard...")
            page.goto(f"{LMS_URL}/my/", timeout=30000)
            page.wait_for_load_state("networkidle", timeout=15000)
            page.wait_for_timeout(3000)

        # ── 4. Ambil HTML dan parse ──────────────────────────────────────────
        html = page.content()
        tasks = self._parse_tasks(html)
        logger.info(f"Found {len(tasks)} tasks")
        return tasks

    def _parse_tasks(self, html: str) -> list[dict]:
        soup = BeautifulSoup(html, "html.parser")
        tasks = []

        # Cari semua container timeline
        # Data tugas ada di dalam div[data-region="event-list-content"]
        event_list = soup.find("div", {"data-region": "event-list-content"})
        if not event_list:
            logger.warning("event-list-content not found in HTML")
            # Fallback: cari langsung event-list-item di seluruh halaman
            event_list = soup

        # Ambil semua item tugas
        # Strukturnya: h5 (tanggal) → list-group → event-list-item (tugas)
        # Kita perlu mapping tanggal ke setiap tugas

        # Cari semua date headers dan item di bawahnya
        # Mereka ada dalam div.border-bottom.pb-2
        date_sections = soup.find_all("div", class_=lambda c: c and "border-bottom" in c and "pb-2" in c)

        if not date_sections:
            # Fallback: ambil semua event-list-item langsung
            logger.info("No date sections found, trying direct item search...")
            items = soup.find_all("div", {"data-region": "event-list-item"})
            for item in items:
                task = self._parse_item(item, deadline_date="")
                if task:
                    tasks.append(task)
            return tasks

        for section in date_sections:
            # Ambil tanggal dari h5
            date_header = section.find("h5")
            deadline_date = ""
            if date_header:
                deadline_date = date_header.get_text(strip=True)

            # Ambil semua tugas di section ini
            items = section.find_all("div", {"data-region": "event-list-item"})
            for item in items:
                task = self._parse_item(item, deadline_date)
                if task:
                    tasks.append(task)

        return tasks

    def _parse_item(self, item, deadline_date: str) -> dict | None:
        try:
            # Judul: dari h6.event-name atau dari title attribute link
            title = ""
            link = ""
            task_id = ""

            title_link = item.find("a", href=lambda h: h and "mod/assign/view.php" in h and "action=" not in h)
            if title_link:
                link = title_link.get("href", "")
                # Ambil title dari attribute, bukan dari text (text bisa ada quotes)
                title = title_link.get("title", "")
                # Bersihkan " is due" suffix
                if " is due" in title:
                    title = title.replace(" is due", "").strip()
                # Fallback ke text content
                if not title:
                    h6 = title_link.find("h6")
                    if h6:
                        title = h6.get_text(strip=True).strip('"')
                    else:
                        title = title_link.get_text(strip=True).strip('"')

                # Ambil ID dari URL
                id_match = re.search(r"[?&]id=(\d+)", link)
                if id_match:
                    task_id = id_match.group(1)

            if not title:
                return None

            # Course: dari small.text-muted
            course = ""
            course_el = item.find("small", class_=lambda c: c and "text-muted" in c)
            if course_el:
                course = course_el.get_text(strip=True).strip('"')

            # Jam deadline: dari small.text-right atau small.text-nowrap
            time_str = ""
            time_el = item.find("small", class_=lambda c: c and ("text-right" in c or "text-nowrap" in c))
            if time_el:
                time_str = time_el.get_text(strip=True)

            # Gabung tanggal + jam
            deadline = deadline_date
            if time_str:
                deadline = f"{deadline_date} {time_str}".strip()

            # Buat ID unik: pakai task_id dari URL, atau hash judul+deadline
            if not task_id:
                task_id = f"{title}_{deadline}"

            return {
                "id": task_id,
                "title": title,
                "course": course,
                "deadline": deadline,
                "link": link,
            }

        except Exception as e:
            logger.error(f"Error parsing item: {e}")
            return None