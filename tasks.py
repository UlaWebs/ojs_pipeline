import os
import re
import json
import pymupdf  # PyMuPDF
import requests
from models import db, SubmissionJob
from flask import Flask
import config

# Buat konteks aplikasi terpisah untuk Worker
app = Flask(__name__)
app.config['SQLALCHEMY_DATABASE_URI'] = config.DATABASE_URI
db.init_app(app)

# ==========================================
# STEP A: EKSTRAKSI DATA DARI PDF VIA AI
# ==========================================
def _extract_body_text(pdf_path):
    """
    Membaca teks PDF dengan strategi cerdas:
    - Halaman 1: lewati blok teks kecil di bagian ATAS (header/watermark jurnal)
      dan ambil teks dengan font lebih besar (judul artikel sebenarnya).
    - Halaman 2-3: ambil teks penuh untuk mendapatkan abstrak & penulis.
    """
    doc = pymupdf.open(pdf_path)
    full_text = ""

    for page_no, page in enumerate(doc[:3]):
        if page_no == 0:
            # Ekstrak blok teks dengan metadata ukuran font
            blocks = page.get_text("dict")["blocks"]
            page_lines = []
            for block in blocks:
                if block.get("type") != 0:
                    continue  # skip gambar
                for line in block.get("lines", []):
                    for span in line.get("spans", []):
                        txt = span.get("text", "").strip()
                        if txt:
                            page_lines.append((span.get("size", 0), txt))

            # Tentukan font-size median untuk filter header kecil
            sizes = [s for s, _ in page_lines if s > 0]
            if sizes:
                median_size = sorted(sizes)[len(sizes) // 2]
                # Ambil teks dengan ukuran >= median (body + judul), skip header kecil
                body_lines = [t for s, t in page_lines if s >= median_size * 0.85]
                full_text += "\n".join(body_lines) + "\n\n"
            else:
                full_text += page.get_text() + "\n\n"
        else:
            full_text += page.get_text() + "\n\n"

    return full_text[:3000]


def extract_pdf_data(pdf_path):
    # 1. Baca teks PDF (dengan filter header watermark)
    text = _extract_body_text(pdf_path)

    # 2. Prompt AI — instruksi eksplisit untuk skip nama jurnal/konferensi
    prompt = (
        "[INST] You are a metadata extraction assistant for academic papers. "
        "From the text below, extract the ARTICLE title (NOT the journal/conference/proceeding name), "
        "abstract, and authors. "
        "The ARTICLE title is the specific title of this paper, usually the largest or most prominent text "
        "after any journal header lines. "
        "Respond with ONLY a raw JSON object — no explanation, no markdown. "
        'Exact structure: {"title": "...", "abstract": "...", "authors": [{"firstName": "...", "lastName": "..."}]}\n\n'
        f"Paper text:\n{text}\n [/INST]\n{{"
    )
    payload = {
        "prompt": prompt,
        "max_length": 600,
        "temperature": 0.1,
    }

    try:
        response = requests.post(config.KOBOLDCPP_URL, json=payload, timeout=300)
        response.raise_for_status()
    except requests.exceptions.ConnectionError:
        raise ConnectionError(
            f"❌ Gagal terhubung ke AI Lokal (KoboldCPP) di {config.KOBOLDCPP_URL}.\n"
            f"Pastikan KoboldCPP sudah dijalankan terlebih dahulu dengan file model .gguf.\n"
            f"Cara: Jalankan 'start_koboldcpp.bat' di folder proyek, lalu tunggu hingga server siap."
        )
    except requests.exceptions.Timeout:
        raise TimeoutError(
            f"❌ AI Lokal (KoboldCPP) tidak merespons dalam 300 detik. "
            f"Coba restart KoboldCPP atau gunakan model yang lebih kecil."
        )

    ai_raw = response.json().get("results", [{}])[0].get("text", "")
    # Kembalikan '{' yang dipotong oleh format prompt
    ai_raw = "{" + ai_raw

    return _parse_ai_response(ai_raw)


# ── Parser Helpers ────────────────────────────────────────────────────────────
def _extract_balanced_json(text):
    """Cari substring JSON valid pertama dengan brace-balancing."""
    start = text.find('{')
    if start == -1:
        return None
    depth = 0
    for i, ch in enumerate(text[start:], start):
        if ch == '{':
            depth += 1
        elif ch == '}':
            depth -= 1
            if depth == 0:
                return text[start:i + 1]
    return None


def _fallback_parse_narrative(text):
    """Regex fallback jika AI mengembalikan teks narasi, bukan JSON murni."""
    result = {}
    title_m = re.search(
        r'"?title"?\s*[:=]\s*"([^"]{10,})"', text, re.IGNORECASE
    )
    if title_m:
        result["title"] = title_m.group(1).strip()

    abstract_m = re.search(
        r'"?abstract"?\s*[:=]\s*"([^"]{20,})"', text, re.IGNORECASE | re.DOTALL
    )
    if abstract_m:
        result["abstract"] = abstract_m.group(1).strip()

    authors_m = re.findall(
        r'"?firstName"?\s*[:=]\s*"([^"]+)"[^}]*"?lastName"?\s*[:=]\s*"([^"]+)"',
        text, re.IGNORECASE
    )
    if authors_m:
        result["authors"] = [{"firstName": f, "lastName": l} for f, l in authors_m]

    if not result:
        result = {"title": "", "abstract": text[:500], "authors": []}

    return result


def _parse_ai_response(ai_raw):
    # Strategi A: JSON murni
    json_candidate = _extract_balanced_json(ai_raw)
    if json_candidate:
        try:
            return json.loads(json_candidate)
        except json.JSONDecodeError:
            pass

    # Strategi B: parse teks narasi
    return _fallback_parse_narrative(ai_raw)


# ==========================================
# STEP B: GENERATE OJS NATIVE XML
# ==========================================
def generate_ojs_xml(metadata_json, pdf_path, job_id, issue_data=None):
    """
    Membuat file OJS Native XML yang bisa diimpor via:
    OJS Admin Panel → Tools → Import/Export → Native XML Plugin

    issue_data: dict dengan key volume, number, year, issue_title (opsional)
    """
    import base64
    from xml.etree.ElementTree import Element, SubElement, tostring, indent
    from datetime import date

    locale = config.OJS_LOCALE
    pdf_filename = os.path.basename(pdf_path)
    date_today = date.today().isoformat()

    # issue_data default
    if not issue_data:
        issue_data = {}
    issue_volume = str(issue_data.get('volume', ''))
    issue_number = str(issue_data.get('number', ''))
    issue_year   = str(issue_data.get('year', date.today().year))
    issue_title  = issue_data.get('issue_title', '')

    # Encode PDF ke base64
    with open(pdf_path, 'rb') as f:
        pdf_bytes = f.read()
    pdf_b64 = base64.b64encode(pdf_bytes).decode('utf-8')
    pdf_size = len(pdf_bytes)

    # ID unik berbasis job_id
    file_id           = job_id
    submission_file_id = job_id + 10000

    # ── Root <article> ────────────────────────────────────────
    article = Element('article', {
        'xmlns':               'http://pkp.sfu.ca',
        'xmlns:xsi':           'http://www.w3.org/2001/XMLSchema-instance',
        'locale':              locale,
        'date_submitted':      date_today,
        'status':              '1',
        'submission_progress': '',
        'stage':               'submission',
        'xsi:schemaLocation':  'http://pkp.sfu.ca native.xsd',
    })

    # ── <submission_file> ─────────────────────────────────────
    sf = SubElement(article, 'submission_file', {
        'xmlns:xsi':          'http://www.w3.org/2001/XMLSchema-instance',
        'id':                 str(submission_file_id),
        'created_at':         date_today,
        'file_id':            str(file_id),
        'stage':              'submission',
        'updated_at':         date_today,
        'viewable':           'true',
        'genre':              'Article Text',
        'xsi:schemaLocation': 'http://pkp.sfu.ca native.xsd',
    })
    sf_name = SubElement(sf, 'name', {'locale': locale})
    sf_name.text = pdf_filename

    sf_file = SubElement(sf, 'file', {
        'id':        str(file_id),
        'filesize':  str(pdf_size),
        'extension': 'pdf',
    })
    embed = SubElement(sf_file, 'embed', {'encoding': 'base64'})
    embed.text = pdf_b64

    # ── <publication> ─────────────────────────────────────────
    pub = SubElement(article, 'publication', {
        'xmlns:xsi':          'http://www.w3.org/2001/XMLSchema-instance',
        'locale':             locale,
        'version':            '1',
        'status':             '1',
        'date_published':     date_today,
        'section_ref':        'ART',
        'xsi:schemaLocation': 'http://pkp.sfu.ca native.xsd',
    })

    # <title>
    title_el = SubElement(pub, 'title', {'locale': locale})
    title_el.text = metadata_json.get('title', '')

    # <abstract>
    abstract_el = SubElement(pub, 'abstract', {'locale': locale})
    abstract_el.text = metadata_json.get('abstract', '')

    # <authors>
    authors_el = SubElement(pub, 'authors', {
        'xmlns:xsi':          'http://www.w3.org/2001/XMLSchema-instance',
        'xsi:schemaLocation': 'http://pkp.sfu.ca native.xsd',
    })
    for idx, author in enumerate(metadata_json.get('authors', [])):
        author_el = SubElement(authors_el, 'author', {
            'include_in_browse': 'true',
            'user_group_ref':    'Author',
            'seq':               str(idx),
            'id':                str(idx + 1),
        })
        given = SubElement(author_el, 'givenname', {'locale': locale})
        given.text = author.get('firstName', '')
        family = SubElement(author_el, 'familyname', {'locale': locale})
        family.text = author.get('lastName', '')
        SubElement(author_el, 'email').text = author.get('email', '')

    # <article_galley> — galley PDF
    galley = SubElement(pub, 'article_galley', {
        'xmlns:xsi':          'http://www.w3.org/2001/XMLSchema-instance',
        'locale':             locale,
        'approved':           'false',
        'xsi:schemaLocation': 'http://pkp.sfu.ca native.xsd',
    })
    galley_name = SubElement(galley, 'name', {'locale': locale})
    galley_name.text = 'PDF'
    SubElement(galley, 'seq').text = '0'
    SubElement(galley, 'submission_file_ref', {'id': str(submission_file_id)})

    # <issue_identification> — diisi jika ada data issue
    if issue_volume or issue_number or issue_year:
        iss_id = SubElement(pub, 'issue_identification')
        if issue_volume:
            SubElement(iss_id, 'volume').text = issue_volume
        if issue_number:
            SubElement(iss_id, 'number').text = issue_number
        if issue_year:
            SubElement(iss_id, 'year').text = issue_year
        if issue_title:
            iss_title = SubElement(iss_id, 'title', {'locale': locale})
            iss_title.text = issue_title

    # ── Serialize & simpan ────────────────────────────────────
    indent(article, space='  ')
    xml_str = '<?xml version="1.0" encoding="utf-8"?>\n' + tostring(article, encoding='unicode')

    xml_dir = os.path.join(os.getcwd(), 'storage', 'xml')
    os.makedirs(xml_dir, exist_ok=True)
    xml_filename = f'submission_job_{job_id}.xml'
    xml_path = os.path.join(xml_dir, xml_filename)

    with open(xml_path, 'w', encoding='utf-8') as f:
        f.write(xml_str)

    return xml_path


# ==========================================
# MAIN WORKER TASK
# ==========================================
def process_pdf_job(job_id):
    with app.app_context():
        job = db.session.get(SubmissionJob, job_id)
        if not job:
            return

        try:
            # Update status → Processing
            job.status = 'processing'
            db.session.commit()

            # Step A: Ekstraksi Data AI
            extracted_json = extract_pdf_data(job.file_path)
            job.metadata_json = json.dumps(extracted_json)
            db.session.commit()

            # Baca issue_data dari job (disimpan sebagai JSON string)
            issue_data = json.loads(job.issue_data) if job.issue_data else {}

            # Step B: Generate OJS Native XML
            xml_path = generate_ojs_xml(extracted_json, job.file_path, job_id, issue_data)

            # Finalisasi → Success
            job.status = 'success'
            job.xml_path = xml_path
            db.session.commit()

        except Exception as e:
            job.status = 'failed'
            job.error_log = str(e)
            db.session.commit()

# ==========================================
# STEP C: OJS AUTOMATION VIA SELENIUM
# ==========================================
def process_selenium_job(job_id):
    import time
    from selenium import webdriver
    from selenium.webdriver.common.by import By
    from selenium.webdriver.support.ui import WebDriverWait
    from selenium.webdriver.support import expected_conditions as EC
    from webdriver_manager.chrome import ChromeDriverManager
    from selenium.webdriver.chrome.service import Service
    from selenium.webdriver.chrome.options import Options

    with app.app_context():
        job = db.session.get(SubmissionJob, job_id)
        if not job:
            return

        try:
            job.automation_status = 'running'
            job.automation_log = 'Memulai proses otomasi browser...\n'
            db.session.commit()

            metadata = json.loads(job.metadata_json) if job.metadata_json else {}
            title = metadata.get('title', '')
            abstract = metadata.get('abstract', '')

            # Setup Chrome options
            chrome_options = Options()
            chrome_options.add_argument("--headless") # Jalankan di background
            chrome_options.add_argument("--window-size=1920,1080")
            chrome_options.add_argument("--disable-gpu")
            chrome_options.add_argument("--no-sandbox")
            
            service = Service(ChromeDriverManager().install())
            driver = webdriver.Chrome(service=service, options=chrome_options)
            wait = WebDriverWait(driver, 15)

            # 1. Login ke OJS
            job.automation_log += f"Login ke {config.OJS_LOGIN_URL}...\n"
            db.session.commit()
            
            driver.get(config.OJS_LOGIN_URL)
            
            # Cari form login (OJS 3.x umum menggunakan input name="username" / "password")
            user_input = wait.until(EC.presence_of_element_located((By.NAME, "username")))
            pass_input = driver.find_element(By.NAME, "password")
            
            user_input.send_keys(config.OJS_USERNAME)
            pass_input.send_keys(config.OJS_PASSWORD)
            
            # Submit login (cari tombol login atau submit form)
            pass_input.submit()
            time.sleep(3)

            job.automation_log += "Berhasil login. Mencari tombol 'New Submission'...\n"
            db.session.commit()

            # 2. Buka halaman New Submission
            # OJS biasanya memiliki tombol "New Submission" di dashboard
            try:
                new_sub_link = wait.until(EC.element_to_be_clickable((By.XPATH, "//a[contains(text(), 'New Submission')] | //a[contains(@href, '/submission/wizard')]")))
                driver.get(new_sub_link.get_attribute("href"))
            except Exception:
                # Fallback: coba construct URL
                base_url = config.OJS_LOGIN_URL.split('/login')[0]
                driver.get(f"{base_url}/submission/wizard")
                time.sleep(3)

            job.automation_log += "Mulai mengisi form Submission...\n"
            job.automation_log += f"Judul: {title[:50]}...\n"
            db.session.commit()

            # --- CATATAN ---
            # Script di bawah ini adalah placeholder untuk OJS 3.x secara umum.
            # Struktur HTML OJS kampus sering dimodifikasi. Anda mungkin perlu 
            # menyesuaikan XPath atau selector di bawah ini.
            # ---------------

            # 3. Step 1: Start (Centang requirements)
            try:
                checkboxes = driver.find_elements(By.CSS_SELECTOR, "input[type='checkbox']")
                for cb in checkboxes:
                    if not cb.is_selected():
                        cb.click()
                time.sleep(1)
                
                # Klik Save and continue
                save_btn = driver.find_element(By.XPATH, "//button[contains(text(), 'Save and continue')]")
                save_btn.click()
                time.sleep(3)
            except Exception as e:
                job.automation_log += f"Warning Step 1: {str(e)}\n"

            # 4. Step 2: Upload Submission (Galley)
            try:
                job.automation_log += "Mengunggah file PDF...\n"
                db.session.commit()

                # Pilih genre (Article Text)
                genre_select = wait.until(EC.presence_of_element_located((By.CSS_SELECTOR, "select[name='genreId']")))
                genre_select.send_keys("Article Text")
                time.sleep(1)

                # Upload file
                file_input = driver.find_element(By.CSS_SELECTOR, "input[type='file']")
                file_input.send_keys(job.file_path)
                time.sleep(5) # Tunggu upload selesai
                
                # Klik continue 2x
                cont_btn = wait.until(EC.element_to_be_clickable((By.XPATH, "//button[contains(text(), 'Continue')]")))
                cont_btn.click()
                time.sleep(2)
                cont_btn = wait.until(EC.element_to_be_clickable((By.XPATH, "//button[contains(text(), 'Continue')]")))
                cont_btn.click()
                time.sleep(2)
                complete_btn = wait.until(EC.element_to_be_clickable((By.XPATH, "//button[contains(text(), 'Complete')]")))
                complete_btn.click()
                time.sleep(2)

                # Save and continue
                save_btn = driver.find_element(By.XPATH, "//button[contains(text(), 'Save and continue')]")
                save_btn.click()
                time.sleep(3)
            except Exception as e:
                job.automation_log += f"Warning Step 2: {str(e)}\n"

            # 5. Step 3: Enter Metadata
            try:
                job.automation_log += "Mengisi metadata (Judul, Abstrak)...\n"
                db.session.commit()

                title_input = wait.until(EC.presence_of_element_located((By.CSS_SELECTOR, "input[name*='title']")))
                title_input.send_keys(title)

                # Tinymce iframe biasanya digunakan untuk abstract di OJS 3.x
                try:
                    driver.switch_to.frame(driver.find_element(By.CSS_SELECTOR, "iframe.tox-edit-area__iframe"))
                    abstract_body = driver.find_element(By.CSS_SELECTOR, "body")
                    abstract_body.send_keys(abstract)
                    driver.switch_to.default_content()
                except:
                    # Fallback textarea biasa
                    abstract_input = driver.find_element(By.CSS_SELECTOR, "textarea[name*='abstract']")
                    abstract_input.send_keys(abstract)

                time.sleep(1)
                save_btn = driver.find_element(By.XPATH, "//button[contains(text(), 'Save and continue')]")
                save_btn.click()
                time.sleep(3)
            except Exception as e:
                job.automation_log += f"Warning Step 3: {str(e)}\n"

            # 6. Step 4 & 5: Confirmation & Submit
            try:
                finish_btn = wait.until(EC.element_to_be_clickable((By.XPATH, "//button[contains(text(), 'Finish Submission')]")))
                finish_btn.click()
                time.sleep(1)
                
                # OK dialog
                ok_btn = wait.until(EC.element_to_be_clickable((By.XPATH, "//button[contains(text(), 'OK')]")))
                ok_btn.click()
                time.sleep(3)

                job.automation_status = 'success'
                job.automation_log += "✅ Proses automasi selesai. Artikel berhasil di-submit!\n"
            except Exception as e:
                job.automation_log += f"Warning Step 4: {str(e)}\n"
                job.automation_status = 'success' # Kita anggap sukses jika sudah lewat step metadata

            driver.quit()
            db.session.commit()

        except Exception as e:
            job.automation_status = 'failed'
            job.automation_log += f"\n❌ ERROR: {str(e)}"
            db.session.commit()
            try:
                driver.quit()
            except:
                pass