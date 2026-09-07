# Panduan Penggunaan Aplikasi OJS Pipeline

## Daftar Isi
1. [Gambaran Umum](#gambaran-umum)
2. [Arsitektur Sistem](#arsitektur-sistem)
3. [Prasyarat](#prasyarat)
4. [Struktur Folder Proyek](#struktur-folder-proyek)
5. [Konfigurasi (File `.env`)](#konfigurasi-file-env)
6. [Cara Menjalankan Aplikasi](#cara-menjalankan-aplikasi)
7. [Dokumentasi API Endpoint](#dokumentasi-api-endpoint)
8. [Alur Kerja End-to-End](#alur-kerja-end-to-end)
9. [Import XML ke OJS](#import-xml-ke-ojs)
10. [Troubleshooting Error Umum](#troubleshooting-error-umum)

---

## Gambaran Umum

**OJS Pipeline** adalah aplikasi berbasis Python/Flask yang mengotomasi proses pengiriman naskah jurnal ke sistem **Open Journal Systems (OJS)**, mulai dari:

1. **Penerimaan file PDF** via endpoint `/upload`.
2. **Ekstraksi metadata** (judul, abstrak, penulis) dari PDF menggunakan **AI Lokal (KoboldCPP)**.
3. **Generate file OJS Native XML** yang siap diimpor ke OJS via fitur Import/Export bawaan.

Semua proses berat dijalankan secara **asynchronous** di background menggunakan **Redis Queue (RQ)**, sehingga client tidak perlu menunggu.

> [!NOTE]
> Pipeline ini menggunakan pendekatan **XML file generation** (bukan REST API) karena lebih kompatibel dengan berbagai konfigurasi OJS kampus yang mungkin tidak mengaktifkan API Key.

---

## Arsitektur Sistem

```
Client (Browser/cURL/Postman)
        │
        │ POST /upload (PDF)
        ▼
┌───────────────────┐      ┌─────────────────┐
│   Flask Server    │ ───► │   Redis Queue   │
│  (app.py:5050)    │      │   (port 6379)   │
└───────────────────┘      └────────┬────────┘
        │                           │ Ambil Job
        │ GET /job/<id>             ▼
        │ GET /job/<id>/    ┌─────────────────┐
        │   download-xml    │  RQ Worker      │
        │ POST /bundle-     │ (run_worker.py) │
        │   issue           └────────┬────────┘
        ▼                            │
┌───────────────────┐       ┌────────▼────────┐      ┌──────────────┐
│  SQLite Database  │       │  Step A: AI     │ ───► │  KoboldCPP   │
│ (ojs_pipeline.db) │       │  Ekstraksi PDF  │      │  (port 5001) │
└───────────────────┘       └────────┬────────┘      └──────────────┘
                                     │
                            ┌────────▼────────┐
                            │  Step B: XML    │
                            │  Generation     │
                            │  storage/xml/   │
                            └─────────────────┘
```

---

## Prasyarat

Pastikan semua komponen berikut tersedia sebelum menjalankan aplikasi:

| Komponen | Kebutuhan | Keterangan |
|:---|:---|:---|
| **Python** | `>= 3.10` | Sudah terpasang |
| **Docker Desktop** | Untuk Redis | Cara termudah menjalankan Redis di Windows |
| **Redis** | Aktif di port `6379` | Dijalankan via Docker |
| **KoboldCPP** | Aktif di port `5001` | AI lokal untuk ekstraksi metadata |
| **Model GGUF** | File `.gguf` | Diletakkan di folder Downloads atau folder pilihan |
| **Akses OJS** | Login sebagai Editor/Manager | Untuk mengimpor XML hasil pipeline |

> [!NOTE]
> Pipeline ini **tidak memerlukan OJS API Key**. Hasil pipeline berupa file XML yang diimpor manual via antarmuka admin OJS.

---

## Struktur Folder Proyek

```
ojs_pipeline/
├── .env                    # Konfigurasi (JANGAN di-commit ke Git)
├── .venv/                  # Virtual environment Python
├── app.py                  # Flask Server & Endpoint API
├── tasks.py                # Logika Worker (Step A & B)
├── models.py               # Model Database SQLAlchemy
├── config.py               # Membaca konfigurasi dari .env
├── run_worker.py           # Skrip menjalankan Worker
├── start_koboldcpp.bat     # Launcher KoboldCPP (AI Server)
├── requirements.txt        # Daftar dependensi Python
└── storage/
    ├── pdfs/               # PDF yang diunggah
    └── xml/                # File OJS Native XML yang dihasilkan
```

---

## Konfigurasi (File `.env`)

Edit file `.env` di root proyek. Semua variabel OJS API sudah dihapus karena tidak diperlukan lagi.

```dotenv
# === DATABASE ===
DATABASE_URI=sqlite:///ojs_pipeline.db

# === REDIS ===
REDIS_HOST=localhost
REDIS_PORT=6379

# === AI (KoboldCPP) ===
KOBOLDCPP_URL=http://localhost:5001/api/v1/generate

# === OJS Output Settings ===
# Locale untuk XML yang dihasilkan (id_ID atau en)
OJS_LOCALE=id_ID
OJS_SECTION_ID=1
```

> [!IMPORTANT]
> Tidak perlu mengisi OJS URL atau API Key. Pipeline sekarang menghasilkan file XML, bukan memanggil REST API OJS.

---

## Cara Menjalankan Aplikasi

Jalankan **4 komponen** secara berurutan, masing-masing di terminal terpisah.

### Langkah 0: Aktifkan Virtual Environment

Lakukan ini di **setiap** terminal baru:

```powershell
# Di folder proyek
.\.venv\Scripts\Activate.ps1
```

Jika muncul error *"execution of scripts is disabled"*:
```powershell
Set-ExecutionPolicy -Scope Process -ExecutionPolicy Bypass
```

---

### Langkah 1: Jalankan Redis (Terminal 1)

> [!NOTE]
> Pastikan **Docker Desktop** sudah berjalan.

```powershell
docker run -d --name redis-ojs -p 6379:6379 redis:alpine
```

Untuk menjalankan ulang jika sudah ada:
```powershell
docker start redis-ojs
```

---

### Langkah 2: Jalankan KoboldCPP — AI Server (Terminal 2)

```powershell
.\start_koboldcpp.bat
```

Tunggu hingga muncul:
```
*** Running API at http://localhost:5001/api/v1/generate ***
```

> [!TIP]
> Model Mistral 7B (~4.2 GB) butuh waktu 30–60 detik untuk load pertama kali.

---

### Langkah 3: Jalankan Flask Server (Terminal 3)

```powershell
.\.venv\Scripts\python.exe app.py
```

Server berjalan di `http://localhost:5050`.

---

### Langkah 4: Jalankan Worker (Terminal 4)

```powershell
.\.venv\Scripts\python.exe run_worker.py
```

Output yang diharapkan:
```
[Worker] Terhubung ke Redis di localhost:6379
[Worker] Mendengarkan antrian: ojs_tasks
```

---

## Dokumentasi API Endpoint

Base URL: `http://localhost:5050`

---

### `POST /upload` — Upload File PDF

Mengunggah PDF dan memasukkannya ke antrian pemrosesan.

**Contoh cURL:**
```bash
curl -X POST http://localhost:5050/upload \
  -F "file=@/path/ke/naskah.pdf"
```

**Contoh PowerShell:**
```powershell
Invoke-RestMethod -Uri "http://localhost:5050/upload" `
  -Method POST `
  -Form @{ file = Get-Item "C:\path\ke\naskah.pdf" }
```

**Response (HTTP 202):**
```json
{
  "message": "File berhasil diunggah dan masuk antrean",
  "job_id": 1,
  "status": "pending"
}
```

> [!TIP]
> Simpan `job_id` untuk mengecek status dan mengunduh XML.

---

### `GET /job/<job_id>` — Cek Status Job

```bash
curl http://localhost:5050/job/1
```

**Response — Status `success` (XML siap diunduh):**
```json
{
  "job_id": 1,
  "filename": "naskah.pdf",
  "status": "success",
  "xml_filename": "submission_job_1.xml",
  "xml_download_url": "/job/1/download-xml",
  "created_at": "2026-09-04T04:10:00+00:00",
  "metadata": {
    "title": "Judul yang Diekstrak AI",
    "abstract": "Abstrak naskah...",
    "authors": [
      { "firstName": "Budi", "lastName": "Santoso" }
    ]
  }
}
```

**Response — Status `failed`:**
```json
{
  "job_id": 1,
  "status": "failed",
  "error_log": "❌ Gagal terhubung ke AI Lokal (KoboldCPP)..."
}
```

**Tabel Status:**

| Status | Arti |
|:---|:---|
| `pending` | Menunggu diproses worker |
| `processing` | Worker sedang ekstraksi PDF |
| `success` | XML berhasil dibuat, siap diunduh |
| `failed` | Error — lihat `error_log` |

---

### `GET /job/<job_id>/download-xml` — Unduh File XML

Mengunduh file OJS Native XML untuk satu artikel.

```bash
curl -O http://localhost:5050/job/1/download-xml
```

File yang diunduh: `submission_job_1.xml`

---

### `POST /bundle-issue` — Bundle Artikel ke 1 Issue XML

Menggabungkan beberapa artikel menjadi **satu file XML issue** yang bisa diimpor sekaligus ke OJS.

**Request:**
```bash
curl -X POST http://localhost:5050/bundle-issue \
  -H "Content-Type: application/json" \
  -d '{
    "job_ids": [1, 2, 3],
    "volume": "13",
    "number": "3",
    "year": "2026",
    "issue_title": "Juni 2026"
  }'
```

**PowerShell:**
```powershell
Invoke-RestMethod -Uri "http://localhost:5050/bundle-issue" `
  -Method POST `
  -ContentType "application/json" `
  -Body '{"job_ids":[1,2,3],"volume":"13","number":"3","year":"2026","issue_title":"Juni 2026"}'
```

Response: file XML terunduh otomatis dengan nama `issue_vol13_no3_2026_jobs1-2-3.xml`.

---

### `GET /jobs` — Daftar Semua Job

```bash
curl http://localhost:5050/jobs
```

```json
[
  {
    "job_id": 2,
    "filename": "naskah2.pdf",
    "status": "success",
    "xml_available": true,
    "xml_download_url": "/job/2/download-xml",
    "created_at": "2026-09-04T05:00:00+00:00"
  }
]
```

---

## Alur Kerja End-to-End

```
1. Upload PDF ke POST /upload
        │
        ▼
2. Flask simpan PDF ke storage/pdfs/
        │
        ▼
3. Flask buat record DB (status: 'pending')
        │
        ▼
4. Flask kirim job_id ke Redis Queue
        │
        ▼  (asynchronous)
        ▼
5. Worker: status → 'processing'
        │
        ▼
6. Worker baca teks PDF (PyMuPDF, 3 halaman)
        │
        ▼
7. Worker kirim ke KoboldCPP → JSON {title, abstract, authors}
        │
        ▼
8. Worker simpan metadata_json ke database
        │
        ▼
9. Worker generate OJS Native XML (dengan PDF ter-embed base64)
   → Simpan ke storage/xml/submission_job_<id>.xml
        │
        ▼
10. Worker: status → 'success', simpan xml_path
        │
        ▼
11. User cek GET /job/<id> → lihat xml_download_url
        │
        ▼
12. User unduh XML via GET /job/<id>/download-xml
        │
        ▼
13. (Opsional) Bundle beberapa artikel via POST /bundle-issue
        │
        ▼
14. Import XML ke OJS (lihat bagian berikut)
```

---

## Import XML ke OJS

### Import Satu Artikel

1. Login ke OJS kampus sebagai **Editor/Manager**
2. Buka **Tools → Import/Export → Native XML Plugin**
3. Klik **"Import"**
4. Pilih file `submission_job_<id>.xml` yang sudah diunduh
5. Klik **"Import"** — artikel akan muncul di daftar submissions

### Import Seluruh Issue Sekaligus

1. Jalankan `POST /bundle-issue` dengan list job_id yang diinginkan
2. Unduh file `issue_vol..._no...xml` yang dikirim sebagai response
3. Login ke OJS → **Tools → Import/Export → Native XML Plugin**
4. Import file issue XML tersebut
5. OJS akan membuat satu issue baru beserta semua artikelnya

> [!IMPORTANT]
> Pastikan **Section Reference** (`ART`) yang ada di XML sudah terdaftar di OJS.
> Jika belum, buat section di OJS terlebih dahulu: **Journal Settings → Sections → Add Section** dengan abbrev `ART`.

---

## Troubleshooting Error Umum

| Error | Penyebab | Solusi |
|:---|:---|:---|
| `Unexpected token '<'` di browser | `app.py` belum di-restart setelah update | Stop `app.py` dengan Ctrl+C, jalankan ulang |
| `ConnectionRefusedError: 6379` | Redis belum berjalan | `docker start redis-ojs` |
| `ConnectionRefusedError: 5001` | KoboldCPP belum berjalan | Jalankan `start_koboldcpp.bat` |
| `AI tidak merespons dalam 300 detik` | Model terlalu lambat | Tunggu lebih lama, atau gunakan model lebih kecil |
| `Model AI tidak menghasilkan format yang dapat di-parse` | Output AI tidak terstruktur | Coba upload ulang — kadang AI perlu beberapa kali percobaan |
| `File XML belum tersedia` | Job belum selesai / masih `processing` | Tunggu job selesai, cek status via GET /job/<id> |
| `Job ID tidak memiliki file XML` (bundle) | Ada job yang belum `success` | Pastikan semua job_id sudah berstatus `success` sebelum bundle |
| `script execution is disabled` (PowerShell) | Kebijakan eksekusi | `Set-ExecutionPolicy -Scope Process -ExecutionPolicy Bypass` |
| Worker tidak memproses job | Worker tidak berjalan | Buka terminal baru, jalankan `run_worker.py` |
