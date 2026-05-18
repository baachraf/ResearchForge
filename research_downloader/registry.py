import sqlite3
import os
from datetime import datetime

class Registry:
    def __init__(self, db_path="downloads_registry.db"):
        self.db_path = db_path
        self._init_db()

    def _init_db(self):
        """Initializes the SQLite database with the necessary schema."""
        with sqlite3.connect(self.db_path) as conn:
            cursor = conn.cursor()
            cursor.execute('''
                CREATE TABLE IF NOT EXISTS downloads (
                    id TEXT PRIMARY KEY,
                    title TEXT,
                    source TEXT,
                    download_path TEXT,
                    downloaded_at TIMESTAMP,
                    url TEXT
                )
            ''')
            conn.commit()

    def is_downloaded(self, paper_id: str) -> bool:
        """Checks if a paper has already been downloaded."""
        with sqlite3.connect(self.db_path) as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT 1 FROM downloads WHERE id = ?", (paper_id,))
            return cursor.fetchone() is not None

    def record_download(self, paper_id: str, title: str, source: str, path: str, url: str):
        """Records a successful download into the registry."""
        with sqlite3.connect(self.db_path) as conn:
            cursor = conn.cursor()
            cursor.execute('''
                INSERT OR REPLACE INTO downloads 
                (id, title, source, download_path, downloaded_at, url) 
                VALUES (?, ?, ?, ?, ?, ?)
            ''', (paper_id, title, source, path, datetime.now().isoformat(), url))
            conn.commit()

    def remove_download(self, paper_id: str):
        """Removes a paper from the registry (used when content filter rejects it post-download)."""
        with sqlite3.connect(self.db_path) as conn:
            cursor = conn.cursor()
            cursor.execute("DELETE FROM downloads WHERE id = ?", (paper_id,))
            conn.commit()

    def get_filepath(self, paper_id: str) -> str:
        """Returns the download path for a paper, or empty string if not found."""
        with sqlite3.connect(self.db_path) as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT download_path FROM downloads WHERE id = ?", (paper_id,))
            row = cursor.fetchone()
            return row[0] if row else ""
