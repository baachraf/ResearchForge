from abc import ABC, abstractmethod
from typing import List, Dict, Any, Optional

class DocumentSource(ABC):
    def __init__(self, credentials: Dict[str, Any] = None):
        """
        Initialize the source with optional credentials (like API keys).
        """
        self.credentials = credentials or {}
    
    @abstractmethod
    def search(self, query: str, max_results: int = 10, language: Optional[str] = None, after_date: Optional[str] = None, force_plus: bool = False) -> List[Dict[str, Any]]:
        """
        Search the source for papers matching the query.
        
        Args:
            query: The search string.
            max_results: Max number of documents to return.
            language: Language code filter.
            after_date: YYYY-MM-DD date string. Only return papers published after this date.
            
        Returns a list of dictionaries with at least the following keys:
        - `id`: Unique identifier for the paper
        - `title`: Title of the paper
        - `url`: Direct link to the PDF
        - `source`: Name of the source
        """
        pass
