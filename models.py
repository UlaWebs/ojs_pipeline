from flask_sqlalchemy import SQLAlchemy
from datetime import datetime, timezone

db = SQLAlchemy()

class SubmissionJob(db.Model):
    __tablename__ = 'submission_jobs'

    id = db.Column(db.Integer, primary_key=True)
    filename = db.Column(db.String(255), nullable=False)
    file_path = db.Column(db.String(500), nullable=False)

    # Status: 'pending', 'processing', 'success', 'failed'
    status = db.Column(db.String(50), default='pending')
    
    # Automation status (Selenium): 'idle', 'running', 'success', 'failed'
    automation_status = db.Column(db.String(50), default='idle')
    automation_log = db.Column(db.Text, nullable=True)

    metadata_json = db.Column(db.Text, nullable=True)   # JSON hasil AI
    xml_path      = db.Column(db.String(500), nullable=True)  # Path OJS Native XML
    issue_data    = db.Column(db.Text, nullable=True)   # JSON: volume/number/year/title

    ojs_submission_id = db.Column(db.Integer, nullable=True)  # (legacy)
    error_log = db.Column(db.Text, nullable=True)

    created_at = db.Column(db.DateTime, default=lambda: datetime.now(timezone.utc))