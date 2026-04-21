import re
import logging
import hashlib
from datetime import datetime
from playwright.sync_api import sync_playwright, TimeoutError as PlaywrightTimeout
from bs4 import BeautifulSoup

logger = logging.getLogger(__name__)


class LMSScraper:
    SIAKAD_LOGIN = "https://siakad.polinema.ac.id/beranda"
    SIAKAD_LMS   = "https://siakad.polinema.ac.id/index.php?r=akademik/lms"
    LMS_DASHBOARD = "https://lmsslc.polinema.ac.id/my/"

    def get_tasks(self, username: str, password: str) -> list[dict]:
        with sync_playwright() as p:
            browser = p.chromium.launch(
                headless=True,
                args=["--no-sandbox", "--disable-dev-shm-usage", "--disable-gpu"]
            )
            context = browser.new_context(
                viewport={"width": 1280, "height": 800},
                user_agent=(
                    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                    "AppleWebKit/537.36 (KHTML, like Gecko) "
                    "Chrome/120.0.0.0 Safari/537.36"
                )
            )
            page = context.new_page()
            try:
                return self._scrape(page, username, password)
            finally:
                browser.close()

    # ─────────────────────────────────────────────────────────────────────────
    def _fill_field(self, page, selectors: list[str], value: str) -> bool:
        """Coba isi field dengan beberapa selector, return True jika berhasil."""
        for sel in selectors:
            try:
                loc = page.locator(sel).first
                if loc.count() and loc.is_visible(timeout=2000):
                    loc.fill(value)
                    logger.info(f"  Filled '{sel}'")
                    return True
            except Exception:
                continue
        return False

    def _click_field(self, page, selectors: list[str]) -> bool:
        """Coba klik elemen dengan beberapa selector."""
        for sel in selectors:
            try:
                loc = page.locator(sel).first
                if loc.count() and loc.is_visible(timeout=2000):
                    loc.click()
                    logger.info(f"  Clicked '{sel}'")
                    return True
            except Exception:
                continue
        return False

    # ─────────────────────────────────────────────────────────────────────────
    def _scrape(self, page, username: str, password: str) -> list[dict]:

        # ── Step 1: Buka halaman login SIAKAD ────────────────────────────
        logger.info("Membuka SIAKAD...")
        page.goto(self.SIAKAD_LOGIN, timeout=30000)
        page.wait_for_load_state("networkidle", timeout=30000)
        logger.info(f"URL awal: {page.url}")

        # ── Step 2: Isi form login ────────────────────────────────────────
        # Coba berbagai selector username yang mungkin dipakai SIAKAD
        username_selectors = [
            'input[name="LoginForm[username]"]',
            'input[name="username"]',
            'input#loginform-username',
            'input#username',
            'input[type="text"]:visible',
        ]
        password_selectors = [
            'input[name="LoginForm[password]"]',
            'input[name="password"]',
            'input#loginform-password',
            'input#password',
            'input[type="password"]:visible',
        ]
        submit_selectors = [
            'button[type="submit"]',
            'input[type="submit"]',
            'button:has-text("Login")',
            'button:has-text("Masuk")',
            'button:has-text("Sign in")',
        ]

        filled_user = self._fill_field(page, username_selectors, username)
        filled_pass = self._fill_field(page, password_selectors, password)

        if not filled_user or not filled_pass:
            logger.warning(f"Gagal isi form: user={filled_user} pass={filled_pass}")
            # Coba screenshot debug jika butuh
        
        self._click_field(page, submit_selectors)
        page.wait_for_load_state("networkidle", timeout=30000)
        logger.info(f"Setelah login: {page.url}")

        # Cek apakah login berhasil
        if "err" in page.url or "login" in page.url:
            raise Exception(
                f"Login SIAKAD gagal (URL: {page.url}). "
                "Cek username/password SIAKAD kamu."
            )

        # ── Step 3: Buka halaman LMS connector ───────────────────────────
        logger.info("Membuka halaman LMS di SIAKAD...")
        try:
            page.goto(self.SIAKAD_LMS, timeout=60000, wait_until="domcontentloaded")
            page.wait_for_load_state("networkidle", timeout=30000)
        except Exception as e:
            logger.warning(f"Gagal buka LMS menu: {e}")

        logger.info(f"URL LMS menu: {page.url}")

        # ── Step 4: Klik Connect to LMS ──────────────────────────────────
        connect_selectors = [
            'a:has-text("Connect to LMS")',
            'button:has-text("Connect to LMS")',
            'a:has-text("LMS Polinema")',
            'a[href*="lmsslc"]',
        ]
        for sel in connect_selectors:
            try:
                loc = page.locator(sel).first
                if loc.count() and loc.is_visible(timeout=3000):
                    logger.info(f"Klik Connect: {sel}")
                    with page.expect_navigation(timeout=30000):
                        loc.click()
                    page.wait_for_load_state("networkidle", timeout=30000)
                    logger.info(f"URL setelah Connect: {page.url}")
                    break
            except Exception as e:
                logger.warning(f"Tombol '{sel}' tidak ada: {e}")

        # ── Step 5: Langsung ke LMS Dashboard ────────────────────────────
        logger.info("Navigasi ke LMS Dashboard...")
        page.goto(self.LMS_DASHBOARD, timeout=30000)
        page.wait_for_load_state("networkidle", timeout=30000)
        logger.info(f"URL Dashboard: {page.url}")

        if "login" in page.url.lower():
            raise Exception(
                "Gagal masuk LMS. SSO mungkin belum terhubung atau "
                "klik Connect to LMS belum berhasil."
            )

        # ── Step 6: Tunggu timeline render ───────────────────────────────
        logger.info("Menunggu timeline render...")
        try:
            page.wait_for_selector(
                '[data-region="event-list-item"]',
                timeout=20000
            )
            page.wait_for_timeout(2000)
            logger.info("Timeline ditemukan!")
        except PlaywrightTimeout:
            logger.warning("Timeout menunggu timeline, lanjut parse HTML...")
            page.wait_for_timeout(3000)

        # ── Step 7: Parse ─────────────────────────────────────────────────
        html = page.content()
        logger.info(f"HTML size: {len(html)} bytes")
        tasks = self._parse_html(html)
        logger.info(f"Total tugas: {len(tasks)}")
        return tasks

    # ─────────────────────────────────────────────────────────────────────────
    def _parse_html(self, html: str) -> list[dict]:
        """
        Struktur HTML LMS Polinema (Moodle):

        div.border-bottom.pb-2          ← section per tanggal
          h5                            ← "Saturday, 11 April 2026"
          div.list-group.list-group-flush
            div[data-region="event-list-item"]
              a[href*="mod/assign"][title="Judul is due"]
                h6.event-name          ← judul (ada quotes di teks)
              small.text-muted         ← nama matkul
              small.text-right         ← "18:00"
        """
        soup = BeautifulSoup(html, "html.parser")
        tasks = []

        # Cari semua section per tanggal
        date_sections = soup.find_all(
            "div",
            class_=lambda c: c and "border-bottom" in c and "pb-2" in c
        )
        logger.info(f"Date sections: {len(date_sections)}")

        if date_sections:
            for section in date_sections:
                h5 = section.find("h5")
                date_str = h5.get_text(strip=True) if h5 else ""

                items = section.find_all("div", {"data-region": "event-list-item"})
                logger.info(f"  '{date_str}' → {len(items)} item")

                for item in items:
                    task = self._parse_item(item, date_str)
                    if task:
                        tasks.append(task)
        else:
            # Fallback: event-list-item tanpa date grouping
            logger.warning("Tidak ada date sections, fallback direct search")
            items = soup.find_all("div", {"data-region": "event-list-item"})
            logger.info(f"Direct items: {len(items)}")
            for item in items:
                task = self._parse_item(item, "")
                if task:
                    tasks.append(task)

        return tasks

    def _parse_item(self, item, date_str: str) -> dict | None:
        try:
            # Link utama tugas — ada href mod/assign tapi BUKAN action=editsubmission
            link_el = item.find(
                "a",
                href=lambda h: h and "mod/assign/view.php" in h and "action=" not in h
            )
            if not link_el:
                return None

            link = link_el.get("href", "")

            # Judul dari title attribute (paling bersih)
            title = link_el.get("title", "")
            if " is due" in title:
                title = title.replace(" is due", "").strip()

            # Fallback ke inner h6
            if not title:
                h6 = link_el.find("h6")
                title = h6.get_text(strip=True).strip('"') if h6 else ""

            if not title:
                return None

            # ID dari ?id= di URL
            m = re.search(r"[?&]id=(\d+)", link)
            task_id = m.group(1) if m else hashlib.md5(
                f"{title}{date_str}".encode()
            ).hexdigest()[:12]

            # Matkul dari small.text-muted
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