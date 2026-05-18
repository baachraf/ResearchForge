import arxiv
from datetime import datetime
from typing import List, Dict, Any, Optional
from .base_source import DocumentSource

class ArxivSource(DocumentSource):
    def __init__(self, credentials: Dict[str, Any] = None):
        super().__init__(credentials)
        self.client = arxiv.Client()
        # Separate client for title lookups: small page_size avoids the 100-result
        # HTTP request that reliably triggers arXiv's rate limiter.
        self._lookup_client = arxiv.Client(page_size=10, delay_seconds=3.0, num_retries=3)

    def search(self, query: str, max_results: int = 10, language: Optional[str] = None, after_date: Optional[str] = None, force_plus: bool = False) -> List[Dict[str, Any]]:
        arxiv_query = query
        if force_plus:
            arxiv_query = query.replace(' ', '+')
        else:
            if ' AND ' not in arxiv_query and ' OR ' not in arxiv_query:
                stripped = arxiv_query.replace('+', ' ').strip()
                words = [word for word in stripped.split() if word]
                if 0 < len(words) <= 6:
                    arxiv_query = " AND ".join(words)

        search = arxiv.Search(
            query=arxiv_query,
            max_results=max_results * 2 if after_date else max_results, # fetch more since we will filter locally
            sort_by=arxiv.SortCriterion.Relevance
        )
        
        filter_date = None
        if after_date:
            try:
                filter_date = datetime.strptime(after_date, "%Y-%m-%d").date()
            except ValueError:
                print("Warning: Invalid date format for Arxiv. Expected YYYY-MM-DD. Ignoring date filter.")
        
        results = []
        try:
            import time as _t
            for attempt in range(3):
                if attempt > 0:
                    wait = 3.0 * (2 ** (attempt - 1))
                    _t.sleep(wait)
                try:
                    for result in self.client.results(search):
                        if filter_date and result.published.date() < filter_date:
                            continue
                        paper_id = result.entry_id.split('/')[-1]
                        results.append({
                            "id": paper_id,
                            "title": result.title,
                            "url": result.pdf_url,
                            "source": "arXiv",
                            "year": result.published.year,
                            "abstract": result.summary,
                            "authors": [a.name for a in result.authors],
                        })
                        if len(results) >= max_results:
                            break
                    break  # success — exit retry loop
                except Exception as e:
                    err = str(e)
                    if "429" in err and attempt < 2:
                        print(f"arXiv rate-limited (429) — retry {attempt+1}/3")
                        continue
                    raise
                    
        except Exception as e:
            print(f"Error checking arXiv: {e}")

        return results

    def lookup_by_title(self, title: str) -> Optional[Dict[str, Any]]:
        """Find a single paper by fuzzy title match using arXiv ti: field search. Returns None if ratio < 0.85.

        Tries quoted phrase first (exact match), then unquoted (handles hyphens,
        colons, and special characters that break quoted arXiv queries).
        """
        import difflib
        title_lower = title.lower()
        # Strip special chars for the unquoted fallback query
        import re as _re
        plain_title = _re.sub(r'[^\w\s]', ' ', title)

        import time as _t
        for query in (f'ti:"{title}"', f'ti:{plain_title}'):
            search = arxiv.Search(
                query=query,
                max_results=10,
                sort_by=arxiv.SortCriterion.Relevance,
            )
            for attempt in range(3):
                if attempt > 0:
                    _t.sleep(3.0 * (2 ** (attempt - 1)))
                try:
                    for result in self._lookup_client.results(search):
                        if result.title and difflib.SequenceMatcher(None, result.title.lower(), title_lower).ratio() >= 0.85:
                            paper_id = result.entry_id.split('/')[-1]
                            return {
                                "id": paper_id,
                                "title": result.title,
                                "url": result.pdf_url,
                                "source": "arXiv",
                                "year": result.published.year,
                                "abstract": result.summary,
                                "authors": [a.name for a in result.authors],
                            }
                    break  # no match in results but no error — try next query variant
                except Exception as e:
                    err = str(e)
                    if "429" in err and attempt < 2:
                        print(f"[LOOKUP] arXiv rate-limited (429) — retry {attempt+1}/3 for query={query!r}")
                        continue
                    print(f"[LOOKUP] arXiv title lookup error (query={query!r}): {e}")
                    break  # non-429 error — try next query variant
        return None
