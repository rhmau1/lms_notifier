# 🎓 LMS Notifier — Polinema

Web app untuk scraping tugas & deadline dari LMS Polinema secara otomatis, dengan notifikasi Telegram.

---

## ✨ Fitur
- 🔍 Scraping tugas dari LMS Polinema (login via SIAKAD)
- 📨 Notifikasi Telegram saat ada tugas baru
- ⏰ Auto-check terjadwal (15–120 menit)
- 📱 Dashboard web yang bisa diakses dari HP
- 🟢/🟡/🔴 Indikator urgensi deadline

---

## 📋 Langkah Setup

### 1. Buat Telegram Bot (wajib!)

1. Buka Telegram, cari **@BotFather**
2. Ketik `/newbot`
3. Masukkan nama bot: misalnya `LMS Polinema Notifier`
4. Masukkan username bot: misalnya `lms_polinema_bot` (harus unik, diakhiri `bot`)
5. **Salin token** yang diberikan BotFather, contoh: `7123456789:AAF-xxxxxxxxxxxxxxxxxxx`

### 2. Dapatkan Chat ID kamu

1. Cari bot **@userinfobot** di Telegram
2. Kirim pesan `/start`
3. **Salin angka Id** yang muncul, contoh: `987654321`

> Alternatif: Setelah bot dibuat, kirim pesan `/start` ke bot kamu,
> lalu buka: `https://api.telegram.org/bot<TOKEN>/getUpdates`
> Chat ID ada di field `message.chat.id`

---

## 🚀 Deploy ke Railway (Gratis)

### Langkah-langkah:

1. **Upload ke GitHub**
   ```bash
   git init
   git add .
   git commit -m "first commit"
   git branch -M main
   git remote add origin https://github.com/USERNAME/lms-notifier.git
   git push -u origin main
   ```

2. **Buka [railway.app](https://railway.app)**
   - Daftar/login dengan GitHub
   - Klik **"New Project"**
   - Pilih **"Deploy from GitHub repo"**
   - Pilih repo `lms-notifier` kamu

3. **Tunggu build** (sekitar 3–5 menit pertama kali)

4. **Dapatkan URL** dari tab **Settings → Domains → Generate Domain**

5. **Buka URL** tersebut dari HP atau laptop ✅

---

## ⚙️ Konfigurasi via Dashboard

Setelah deploy, buka URL web app-mu:

1. **Isi Kredensial:**
   - Username SIAKAD: NIM kamu
   - Password SIAKAD: password login siakad.polinema.ac.id
   - Telegram Bot Token: token dari BotFather
   - Telegram Chat ID: ID kamu

2. **Klik Simpan**

3. **Test Telegram** → pastikan pesan test masuk ke HP

4. **Klik Cek Sekarang** → scraping pertama kali

5. **Aktifkan Auto-check** → set interval, toggle ON

---

## 🔧 Environment Variables (Opsional)

Jika mau credentials otomatis ter-load saat start, set di Railway:
```
SIAKAD_USERNAME=NIM_KAMU
SIAKAD_PASSWORD=PASSWORD_KAMU
TELEGRAM_TOKEN=TOKEN_BOT_KAMU
TELEGRAM_CHAT_ID=CHAT_ID_KAMU
SECRET_KEY=random_string_bebas
```

Di Railway: Settings → Variables → Add Variable

---

## 📱 Contoh Notifikasi Telegram

```
📚 Tugas Baru Ditemukan!

📝 Milestone 1 2C
🏫 Analisis dan Desain Berorientasi Objek : Diploma IV Teknik Informatika
⏰ Deadline: 21 Apr 2026 18:00
🔗 Buka Tugas
```

---

## ❓ Troubleshooting

| Masalah | Solusi |
|---------|--------|
| Login gagal | Cek username/password SIAKAD |
| Tugas tidak muncul | LMS mungkin lambat, coba lagi |
| Telegram tidak kirim | Cek token & chat ID, pastikan sudah `/start` bot |
| Build Railway gagal | Pastikan semua file ada, cek log di Railway |

---

## ⚠️ Catatan Penting

- Kredensial **tidak disimpan permanen** ke database, hanya di memory.
  Isi ulang jika server restart, atau gunakan Environment Variables.
- Scraping berjalan di background, jangan tutup tab saat proses berjalan.
- Pastikan tidak melanggar kebijakan penggunaan LMS Polinema.
