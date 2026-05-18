import os
import requests
from tqdm import tqdm
from .registry import Registry
from .pdf_resolver import resolve_pdf_url, is_direct_pdf

class Downloader:
    def __init__(self, registry: Registry, max_size_mb: float = None):
        """
        Initializes the Downloader.
        :param registry: The Registry instance for tracking downloads.
        :param max_size_mb: Maximum allowed file size in MB. None for no limit.
        """
        self.registry = registry
        self.max_size_bytes = max_size_mb * 1024 * 1024 if max_size_mb else float('inf')

    def download(self, paper_id: str, title: str, url: str, source: str, target_dir: str, year: int = None):
        """
        Downloads the PDF incrementally.
        If the URL is a landing page (not a direct PDF), attempts to discover
        the actual PDF URL via pdf_resolver before downloading.
        Returns the filepath string if newly downloaded, False if skipped/failed.
        """
        if self.registry.is_downloaded(paper_id):
            print(f"Skipping {paper_id} - Already downloaded.")
            return False

        # Resolve landing pages to a direct PDF URL
        if not is_direct_pdf(url):
            print(f"[Resolver] Discovering PDF for: {url[:100]}")
            resolved = resolve_pdf_url(url)
            if resolved:
                print(f"[Resolver] Found: {resolved[:100]}")
                url = resolved
            else:
                print(f"[Resolver] Could not find PDF — skipping {paper_id}")
                return False

        # Check size before downloading
        try:
            head_req = requests.head(url, allow_redirects=True, timeout=10)
            if head_req.status_code == 200 and 'Content-Length' in head_req.headers:
                size_bytes = int(head_req.headers['Content-Length'])
                if size_bytes > self.max_size_bytes:
                    print(f"Skipping {paper_id} - File size ({size_bytes / (1024*1024):.2f} MB) exceeds limit.")
                    return False
        except Exception as e:
            print(f"Failed to check size for {paper_id}: {e}")
            # If we fail to check size, we can proceed to download speculatively,
            # but checking stream inside the download loop.

        os.makedirs(target_dir, exist_ok=True)
        # Create safe filename: YEAR_Source_Title.pdf (no hash in filename)
        safe_title = "".join([c if c.isalnum() else "_" for c in title])[:60]
        year_prefix = str(year) if year else "unknown"
        filename = f"{year_prefix}_{source}_{safe_title}.pdf"
        filepath = os.path.join(target_dir, filename)

        try:
            with requests.get(url, stream=True, allow_redirects=True, timeout=30) as r:
                r.raise_for_status()
                total_length = int(r.headers.get('content-length', 0))
                
                # Double-check size just in case HEAD didn't work but GET stream sees it
                if total_length > self.max_size_bytes:
                    print(f"Skipping {paper_id} - File size ({total_length / (1024*1024):.2f} MB) exceeds limit upon stream start.")
                    return False

                with open(filepath, 'wb') as f:
                    if total_length == 0:
                        f.write(r.content)
                    else:
                        with tqdm(total=total_length, unit='iB', unit_scale=True, desc=f"Downloading {filename}") as pbar:
                            for data in r.iter_content(chunk_size=4096):
                                f.write(data)
                                pbar.update(len(data))
            
            # Record success — return the filepath so callers can inspect/filter
            self.registry.record_download(paper_id, title, source, filepath, url)
            return filepath

        except Exception as e:
            print(f"Error downloading {paper_id}: {e}")
            if os.path.exists(filepath):
                os.remove(filepath)  # clean up partial
            return False
