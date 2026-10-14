#!/usr/bin/env python3
"""Download and parse PDFs into the FSSA corpus."""
import json
import os
import re
import sys
import urllib.request
import pymupdf

CORPUS_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "corpus.json")
PDF_LIST_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "pdf_urls.json")

def clean_text(text):
    # Remove excessive whitespace, keep structure
    text = re.sub(r'\n{3,}', '\n\n', text)
    text = re.sub(r' {2,}', ' ', text)
    return text.strip()

def parse_pdf(url, title_hint=""):
    """Download and extract text from a PDF URL."""
    print(f"Downloading: {url[:80]}...")
    try:
        req = urllib.request.Request(url, headers={'User-Agent': 'Mozilla/5.0'})
        with urllib.request.urlopen(req, timeout=60) as resp:
            data = resp.read()
        
        doc = pymupdf.open(stream=data, filetype="pdf")
        pages_text = []
        for i, page in enumerate(doc):
            text = page.get_text()
            if text.strip():
                pages_text.append(f"[Page {i+1}]\n{text}")
        
        full_text = "\n\n".join(pages_text)
        full_text = clean_text(full_text)
        
        print(f"  Extracted {len(full_text)} chars from {len(pages_text)} pages")
        return {
            "url": url,
            "title": title_hint or url.split("/")[-1].replace(".pdf", "").replace("-", " ").replace("_", " ").title(),
            "text": full_text,
            "source": "pdf",
            "doc_type": "pdf"
        }
    except Exception as e:
        print(f"  ERROR: {e}")
        return None

def main():
    if not os.path.exists(PDF_LIST_PATH):
        print(f"PDF list not found: {PDF_LIST_PATH}")
        print("Create it with: [{\"url\": \"...\", \"title\": \"...\"}, ...]")
        sys.exit(1)
    
    pdf_list = json.load(open(PDF_LIST_PATH))
    print(f"Found {len(pdf_list)} PDFs to process")
    
    # Load existing corpus
    corpus = json.load(open(CORPUS_PATH))
    existing_urls = {d.get("url") for d in corpus}
    
    new_docs = []
    for item in pdf_list:
        url = item["url"]
        if url in existing_urls:
            print(f"Skipping (already in corpus): {url[:60]}")
            continue
        
        doc = parse_pdf(url, item.get("title", ""))
        if doc and len(doc["text"]) > 500:  # Skip empty/failed
            new_docs.append(doc)
            existing_urls.add(url)
    
    if new_docs:
        corpus.extend(new_docs)
        json.dump(corpus, open(CORPUS_PATH, "w"), indent=1)
        print(f"\nAdded {len(new_docs)} PDF docs to corpus. Total: {len(corpus)}")
    else:
        print("\nNo new docs added.")

if __name__ == "__main__":
    main()
