import re
import logging
import hashlib
from datetime import datetime
from playwright.sync_api import sync_playwright, TimeoutError as PlaywrightTimeout
from bs4 import BeautifulSoup

logger = logging.getLogger(__name__)


class LMSScraper:
    SIAKAD_URL = "https://siakad.polinema.ac.id/beranda"
    LMS_DASHBOARD = "https://lmsslc.polinema.ac.id/my/"

    def get_tasks(self, username: str, password: str) -> list[dict]:
        with sync_playwright() as p:
            browser = p.chromium.launch(
                headless=True,
                args=["--no-sandbox", "--disable-dev-shm-usage", "--disable-gpu"]
            )
            context = browser.new_context(
                viewport={"width": 1280, "height": 800},
                user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
            )
            page = context.new_page()
            try:
                tasks = self._scrape(page, username, password)
                return tasks
            finally:
                browser.close()

    def _scrape(self, page, username: str, password: str) -> list[dict]:
        # ── Step 1: Login SIAKAD ──────────────────────────────────────────
        logger.info("Login ke SIAKAD...")
        page.goto(self.SIAKAD_URL, timeout=30000)
        page.wait_for_load_state("networkidle", timeout=30000)

        page.fill('input[name="username"], input[type="text"]', username)
        page.fill('input[name="password"], input[type="password"]', password)
        page.click('button[type="submit"], input[type="submit"]')
        page.wait_for_load_state("networkidle", timeout=30000)
        logger.info(f"Setelah login SIAKAD: {page.url}")

        # ── Step 2: Buka halaman LMS connector di SIAKAD ─────────────────
        logger.info("Membuka halaman LMS di SIAKAD...")
        try:
            page.goto(
                "https://siakad.polinema.ac.id/index.php?r=akademik/lms",
                timeout=60000,
                wait_until="domcontentloaded"
            )
            page.wait_for_load_state("networkidle", timeout=30000)
        except Exception as e:
            logger.warning(f"Gagal memuat menu LMS: {e}")

        logger.info(f"URL setelah buka LMS menu: {page.url}")

        # ── Step 3: Klik tombol Connect to LMS ───────────────────────────
        try:
            btn = page.locator('a:has-text("Connect to LMS"), button:has-text("Connect to LMS")')
            if btn.count() > 0:
                logger.info("Klik Connect to LMS...")
                with page.expect_navigation(timeout=30000):
                    btn.first.click()
                page.wait_for_load_state("networkidle", timeout=30000)
                logger.info(f"URL setelah klik Connect: {page.url}")
        except PlaywrightTimeout:
            logger.warning("Timeout saat klik Connect to LMS")
        except Exception as e:
            logger.warning(f"Tidak ada tombol Connect: {e}")

        # ── Step 4: Langsung ke LMS Dashboard ────────────────────────────
        logger.info("Navigasi ke LMS Dashboard...")
        page.goto(self.LMS_DASHBOARD, timeout=30000)
        page.wait_for_load_state("networkidle", timeout=30000)
        logger.info(f"URL dashboard: {page.url}")

        if "login" in page.url.lower():
            raise Exception("Login gagal! Cek username/password SIAKAD.")

        # ── Step 5: Tunggu timeline block render ─────────────────────────
        logger.info("Menunggu timeline...")
        try:
            page.wait_for_selector('[data-region="event-list-item"]', timeout=20000)
            page.wait_for_timeout(2000)
        except PlaywrightTimeout:
            logger.warning("Timeline belum muncul, tunggu lebih lama...")
            page.wait_for_timeout(5000)

        # ── Step 6: Parse HTML ────────────────────────────────────────────
        html = page.content()
        logger.info(f"HTML length: {len(html)}")

        tasks = self._parse_html(html)
        logger.info(f"Ditemukan {len(tasks)} tugas")
        return tasks

    def _parse_html(self, html: str) -> list[dict]:
        soup = BeautifulSoup(html, "html.parser")
        tasks = []

        # Struktur HTML LMS Polinema:
        # div.border-bottom.pb-2
        #   └── h5 (tanggal, misal "Saturday, 11 April 2026")
        #   └── div.list-group
        #         └── div[data-region="event-list-item"]
        #               └── a[href*="mod/assign"] (title = "Judul is due")
        #               └── small.text-muted (nama matkul)
        #               └── small.text-right (jam, misal "18:00")

        # Cari semua section per tanggal
        date_sections = soup.find_all(
            "div",
            class_=lambda c: c and "border-bottom" in c and "pb-2" in c
        )

        logger.info(f"Date sections ditemukan: {len(date_sections)}")

        if not date_sections:
            # Fallback: cari event-list-item langsung tanpa date grouping
            logger.warning("Tidak ada date sections, fallback ke direct item search")
            items = soup.find_all("div", {"data-region": "event-list-item"})
            logger.info(f"Direct items: {len(items)}")
            for item in items:
                task = self._parse_item(item, "")
                if task:
                    tasks.append(task)
            return tasks

        for section in date_sections:
            # Ambil tanggal dari h5
            h5 = section.find("h5")
            date_str = h5.get_text(strip=True) if h5 else ""

            # Ambil semua item di section ini
            items = section.find_all("div", {"data-region": "event-list-item"})
            logger.info(f"Tanggal '{date_str}': {len(items)} item")

            for item in items:
                task = self._parse_item(item, date_str)
                if task:
                    tasks.append(task)

        return tasks

    def _parse_item(self, item, date_str: str) -> dict | None:
        try:
            # Ambil link utama tugas (bukan link "Add submission")
            # Link tugas: href mengandung mod/assign/view.php tapi TIDAK ada action=
            link_el = item.find(
                "a",
                href=lambda h: h and "mod/assign/view.php" in h and "action=" not in h
            )

            if not link_el:
                return None

            link = link_el.get("href", "")

            # Judul: dari title attribute (lebih bersih, tanpa quotes)
            title = link_el.get("title", "")
            if " is due" in title:
                title = title.replace(" is due", "").strip()

            # Fallback ke inner h6 text
            if not title:
                h6 = link_el.find("h6")
                if h6:
                    title = h6.get_text(strip=True).strip('"')

            if not title:
                return None

            # ID dari URL parameter ?id=
            id_match = re.search(r"[?&]id=(\d+)", link)
            task_id = id_match.group(1) if id_match else hashlib.md5(
                f"{title}{date_str}".encode()
            ).hexdigest()[:12]

            # Course dari small.text-muted
            course_el = item.find("small", class_=lambda c: c and "text-muted" in c)
            course = course_el.get_text(strip=True).strip('"') if course_el else ""

            # Jam dari small.text-right atau small.text-nowrap
            time_el = item.find(
                "small",
                class_=lambda c: c and ("text-right" in c or "text-nowrap" in c)
            )
            time_str = time_el.get_text(strip=True) if time_el else ""

            deadline = f"{date_str} {time_str}".strip() if time_str else date_str

            return {
                "id": task_id,
                "title": title,
                "course": course,
                "deadline": deadline,
                "link": link,
                "scraped_at": datetime.now().isoformat(),
            }

        except Exception as e:
            logger.error(f"Error parse item: {e}")
            return None