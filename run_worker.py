"""
run_worker.py — Worker RQ yang kompatibel dengan Windows.

Di Windows, RQ Worker standar tidak bisa digunakan karena membutuhkan
os.fork() yang hanya tersedia di Linux/macOS. Sebagai gantinya, 
gunakan SimpleWorker yang berjalan secara single-thread di proses yang sama.

Cara menjalankan:
    .\.venv\Scripts\python.exe run_worker.py
"""

from redis import Redis
from rq import Queue, SimpleWorker
import config

def main():
    redis_conn = Redis(host=config.REDIS_HOST, port=config.REDIS_PORT)
    queues = [Queue('ojs_tasks', connection=redis_conn)]

    print(f"[Worker] Terhubung ke Redis di {config.REDIS_HOST}:{config.REDIS_PORT}")
    print(f"[Worker] Mendengarkan antrian: ojs_tasks")
    print(f"[Worker] Gunakan Ctrl+C untuk berhenti.\n")

    # SimpleWorker: mode single-process, kompatibel dengan Windows
    worker = SimpleWorker(queues, connection=redis_conn)
    worker.work(with_scheduler=False)

if __name__ == '__main__':
    main()
