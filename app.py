import os
import json
from flask import Flask, request, jsonify, render_template, send_file
from werkzeug.utils import secure_filename
from redis import Redis
from rq import Queue

from models import db, SubmissionJob
from tasks import process_pdf_job
import config

app = Flask(__name__)
app.config['SQLALCHEMY_DATABASE_URI'] = config.DATABASE_URI
app.config['UPLOAD_FOLDER'] = os.path.join(os.getcwd(), 'storage', 'pdfs')
os.makedirs(app.config['UPLOAD_FOLDER'], exist_ok=True)

db.init_app(app)

redis_conn = Redis(host=config.REDIS_HOST, port=config.REDIS_PORT)
task_queue = Queue('ojs_tasks', connection=redis_conn)

with app.app_context():
    db.create_all()


# ==========================================
# ROUTE: Dashboard UI
# ==========================================
@app.route('/', methods=['GET'])
def index():
    return render_template('index.html')


# ==========================================
# ROUTE: Upload PDF (mendukung 1-10 file)
# ==========================================
@app.route('/upload', methods=['POST'])
def upload_pdf():
    """
    Menerima 1–10 file PDF sekaligus + metadata issue.
    Form fields:
      - file[]       : satu atau lebih PDF
      - volume       : (opsional) nomor volume
      - number       : (opsional) nomor edisi
      - year         : (opsional) tahun terbit
      - issue_title  : (opsional) judul edisi, contoh: "Juni 2026"
    """
    files = request.files.getlist('file[]')
    if not files or all(f.filename == '' for f in files):
        # Coba field 'file' tunggal untuk kompatibilitas mundur
        single = request.files.get('file')
        if single and single.filename:
            files = [single]
        else:
            return jsonify({'error': 'Tidak ada file dikirim'}), 400

    # Batasi maksimal 10 file
    if len(files) > 10:
        return jsonify({'error': 'Maksimal 10 file per sekali upload'}), 400

    # Kumpulkan issue_data dari form
    issue_data = {
        'volume':      request.form.get('volume', '').strip(),
        'number':      request.form.get('number', '').strip(),
        'year':        request.form.get('year', '').strip(),
        'issue_title': request.form.get('issue_title', '').strip(),
    }
    issue_data_str = json.dumps(issue_data)

    created_jobs = []
    errors = []

    for file in files:
        if not file.filename.lower().endswith('.pdf'):
            errors.append({'filename': file.filename, 'error': 'Bukan file PDF'})
            continue

        filename = secure_filename(file.filename)
        saved_path = os.path.join(app.config['UPLOAD_FOLDER'], filename)
        file.save(saved_path)

        new_job = SubmissionJob(
            filename=filename,
            file_path=saved_path,
            status='pending',
            issue_data=issue_data_str,
        )
        db.session.add(new_job)
        db.session.commit()

        task_queue.enqueue(process_pdf_job, new_job.id)
        created_jobs.append({'job_id': new_job.id, 'filename': filename})

    if not created_jobs:
        return jsonify({'error': 'Tidak ada file valid yang berhasil diunggah', 'details': errors}), 400

    return jsonify({
        'message': f'{len(created_jobs)} file berhasil diunggah dan masuk antrean',
        'jobs': created_jobs,
        'issue_data': issue_data,
        'errors': errors,
    }), 202


# ==========================================
# ROUTE: Cek Status Job
# ==========================================
@app.route('/job/<int:job_id>', methods=['GET'])
def get_job_status(job_id):
    job = db.session.get(SubmissionJob, job_id)
    if not job:
        return jsonify({'error': f'Job ID {job_id} tidak ditemukan'}), 404

    response = {
        'job_id':   job.id,
        'filename': job.filename,
        'status':   job.status,
        'automation_status': job.automation_status,
        'created_at': job.created_at.isoformat() if job.created_at else None,
    }

    if job.issue_data:
        response['issue_data'] = json.loads(job.issue_data)

    if job.xml_path and os.path.exists(job.xml_path):
        response['xml_filename']    = os.path.basename(job.xml_path)
        response['xml_download_url'] = f'/job/{job_id}/download-xml'

    if job.metadata_json:
        response['metadata'] = json.loads(job.metadata_json)

    if job.status == 'failed':
        response['error_log'] = job.error_log

    if job.automation_log:
        response['automation_log'] = job.automation_log

    return jsonify(response), 200


# ==========================================
# ROUTE: Download File XML (per artikel)
# ==========================================
@app.route('/job/<int:job_id>/download-xml', methods=['GET'])
def download_xml(job_id):
    job = db.session.get(SubmissionJob, job_id)
    if not job:
        return jsonify({'error': f'Job ID {job_id} tidak ditemukan'}), 404
    if not job.xml_path or not os.path.exists(job.xml_path):
        return jsonify({'error': 'File XML belum tersedia. Pastikan job sudah selesai (status: success).'}), 404

    return send_file(
        job.xml_path,
        mimetype='application/xml',
        as_attachment=True,
        download_name=os.path.basename(job.xml_path)
    )


# ==========================================
# ROUTE: Jalankan Automasi Selenium
# ==========================================
@app.route('/job/<int:job_id>/automate', methods=['POST'])
def automate_job(job_id):
    job = db.session.get(SubmissionJob, job_id)
    if not job:
        return jsonify({'error': f'Job ID {job_id} tidak ditemukan'}), 404
        
    if job.status != 'success':
        return jsonify({'error': 'Job belum berstatus success (ekstraksi belum selesai).'}), 400
        
    if job.automation_status == 'running':
        return jsonify({'error': 'Automasi sedang berjalan.'}), 400

    job.automation_status = 'pending'
    job.automation_log = ''
    db.session.commit()

    from tasks import process_selenium_job
    task_queue.enqueue(process_selenium_job, job.id)

    return jsonify({'message': 'Proses automasi dijadwalkan.', 'job_id': job_id}), 202



# ==========================================
# ROUTE: Bundle Artikel ke 1 Issue XML
# ==========================================
@app.route('/bundle-issue', methods=['POST'])
def bundle_issue():
    """
    Menggabungkan beberapa artikel menjadi satu file XML Issue OJS.

    Body JSON:
    {
        "job_ids": [1, 2, 3],
        "volume": "13",
        "number": "3",
        "year": "2026",
        "issue_title": "Juni 2026"
    }
    """
    data = request.get_json()
    if not data:
        return jsonify({'error': 'Body JSON diperlukan'}), 400

    job_ids = data.get('job_ids', [])
    if not job_ids:
        return jsonify({'error': 'job_ids tidak boleh kosong'}), 400

    volume      = str(data.get('volume', ''))
    number      = str(data.get('number', ''))
    year        = str(data.get('year', ''))
    issue_title = data.get('issue_title', f'Vol. {volume} No. {number} ({year})')

    from xml.etree.ElementTree import Element, SubElement, tostring, indent, parse
    from datetime import date

    ns  = 'http://pkp.sfu.ca'
    xsi = 'http://www.w3.org/2001/XMLSchema-instance'

    issue = Element('issue', {
        'xmlns':              ns,
        'xmlns:xsi':          xsi,
        'published':          '1',
        'current':            '0',
        'access_status':      '1',
        'url_path':           '',
        'xsi:schemaLocation': f'{ns} native.xsd',
    })

    ident = SubElement(issue, 'issue_identification')
    SubElement(ident, 'volume').text = volume
    SubElement(ident, 'number').text = number
    SubElement(ident, 'year').text   = year
    title_el = SubElement(ident, 'title', {'locale': config.OJS_LOCALE})
    title_el.text = issue_title
    SubElement(issue, 'date_published').text = date.today().isoformat()

    articles_el = SubElement(issue, 'articles', {
        'xmlns:xsi':          xsi,
        'xsi:schemaLocation': f'{ns} native.xsd',
    })

    missing = []
    for jid in job_ids:
        job = db.session.get(SubmissionJob, jid)
        if not job or not job.xml_path or not os.path.exists(job.xml_path):
            missing.append(jid)
            continue
        tree = parse(job.xml_path)
        articles_el.append(tree.getroot())

    if missing:
        return jsonify({
            'error': (
                f'Job ID berikut tidak memiliki file XML yang valid: {missing}. '
                'Pastikan semua job sudah berstatus "success".'
            )
        }), 400

    indent(issue, space='  ')
    xml_str = '<?xml version="1.0" encoding="utf-8"?>\n' + tostring(issue, encoding='unicode')

    xml_dir = os.path.join(os.getcwd(), 'storage', 'xml')
    os.makedirs(xml_dir, exist_ok=True)
    ids_str = '-'.join(str(i) for i in job_ids)
    xml_filename = f'issue_vol{volume}_no{number}_{year}_jobs{ids_str}.xml'
    xml_path = os.path.join(xml_dir, xml_filename)
    with open(xml_path, 'w', encoding='utf-8') as f:
        f.write(xml_str)

    return send_file(
        xml_path,
        mimetype='application/xml',
        as_attachment=True,
        download_name=xml_filename
    )


# ==========================================
# ROUTE: Daftar Semua Job
# ==========================================
@app.route('/jobs', methods=['GET'])
def list_jobs():
    jobs = SubmissionJob.query.order_by(SubmissionJob.created_at.desc()).all()
    result = []
    for j in jobs:
        item = {
            'job_id':          j.id,
            'filename':        j.filename,
            'status':          j.status,
            'automation_status': j.automation_status,
            'xml_available':   bool(j.xml_path and os.path.exists(j.xml_path)),
            'xml_download_url': (
                f'/job/{j.id}/download-xml'
                if j.xml_path and os.path.exists(j.xml_path) else None
            ),
            'created_at': j.created_at.isoformat() if j.created_at else None,
        }
        if j.issue_data:
            item['issue_data'] = json.loads(j.issue_data)
        result.append(item)
    return jsonify(result), 200


if __name__ == '__main__':
    app.run(port=5050, debug=True)