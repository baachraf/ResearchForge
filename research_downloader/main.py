"""
main.py — Research Paper Downloader Core
=========================================
Call run(CONFIG) from your project file. Never edit this file for a new project.

CONFIG dict structure:
  output_root   : str   — root folder where all topic subfolders will be created
  keys_to_run   : list  — which query keys to execute (empty = all)
  credentials   : dict  — brave_search / semantic_scholar / arxiv keys & flags
  queries       : dict  — {key: {query, language, after_date, max_results,
                                  output_folder, max_size_mb, sources,
                                  must_contain, relevance_threshold, force_plus}}
                          output_folder is a subfolder name under output_root
"""

import os

from .registry import Registry
from .downloader import Downloader
from .sources.arxiv_source import ArxivSource
from .sources.semantic_scholar_source import SemanticScholarSource
from .sources.web_source import WebSource
from .sources.brave_source import BraveSource
from .relevance_filter import passes_title_filter, passes_content_filter


def run(config: dict):
    output_root        = config["output_root"]
    queries            = config.get("queries", {})
    credentials_config = config.get("credentials", {})
    keys_to_run        = config.get("keys_to_run") or list(queries.keys())

    os.makedirs(output_root, exist_ok=True)

    print(f"\nOutput root : {output_root}")
    print(f"Keys        : {len(keys_to_run)} selected / {len(queries)} total\n")

    # ── Initialize sources ────────────────────────────────────────────────────
    available_sources = {"web": WebSource()}

    brave_creds = credentials_config.get("brave_search", {})
    if brave_creds.get("enabled", True) and brave_creds.get("api_key"):
        available_sources["brave"] = BraveSource(credentials=brave_creds)

    arxiv_creds = credentials_config.get("arxiv", {})
    if arxiv_creds.get("enabled", True):
        available_sources["arxiv"] = ArxivSource(credentials=arxiv_creds)

    s2_creds = credentials_config.get("semantic_scholar", {})
    if s2_creds.get("enabled", True):
        available_sources["semantic_scholar"] = SemanticScholarSource(credentials=s2_creds)

    # ── Run each key ──────────────────────────────────────────────────────────
    for current_key in keys_to_run:
        if current_key not in queries:
            print(f"Warning: key '{current_key}' not found in queries. Skipping.")
            continue

        q = queries[current_key]

        print(f"\n{'='*44}")
        print(f"Key: {current_key}")
        print(f"{'='*44}")

        search_term         = q.get("query", current_key.replace("_", " "))
        language            = q.get("language")
        after_date          = q.get("after_date")
        max_results         = q.get("max_results", 20)
        max_size_mb         = q.get("max_size_mb", 10.0)
        sources_to_use      = q.get("sources", ["arxiv", "semantic_scholar"])
        force_plus          = q.get("force_plus", False)
        must_contain        = q.get("must_contain", [])
        relevance_threshold = q.get("relevance_threshold", 1)

        # output_folder is a subfolder name under output_root
        subfolder  = q.get("output_folder", current_key)
        target_dir = os.path.join(output_root, subfolder)
        os.makedirs(target_dir, exist_ok=True)

        registry   = Registry(os.path.join(target_dir, "downloads_registry.db"))
        downloader = Downloader(registry=registry, max_size_mb=max_size_mb)

        for source_name, source_obj in available_sources.items():
            if source_name not in sources_to_use:
                continue

            print(f"\n--- {source_name.title()} | '{search_term}' ---")

            results = source_obj.search(
                query=search_term,
                max_results=max_results,
                language=language,
                after_date=after_date,
                force_plus=force_plus,
            )
            print(f"Found {len(results)} potential papers.")

            new_dl = rejected_title = rejected_content = 0

            for paper in results:
                if must_contain and not passes_title_filter(paper["title"], must_contain):
                    print(f"  [Title] Rejected: {paper['title'][:70]}")
                    rejected_title += 1
                    continue

                filepath = downloader.download(
                    paper_id=paper["id"],
                    title=paper["title"],
                    url=paper["url"],
                    source=paper["source"],
                    target_dir=target_dir,
                    year=paper.get("year"),
                )

                if filepath and isinstance(filepath, str):
                    if must_contain and not passes_content_filter(
                            filepath, must_contain, relevance_threshold):
                        print(f"  [Content] Rejected & deleted: {paper['title'][:70]}")
                        os.remove(filepath)
                        registry.remove_download(paper["id"])
                        rejected_content += 1
                    else:
                        new_dl += 1
                elif filepath is True:
                    new_dl += 1

            print(f"  → {new_dl} new | {rejected_title} title-rej | "
                  f"{rejected_content} content-rej")
