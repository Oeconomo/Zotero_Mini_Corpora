#!/usr/bin/env python3
"""
PhD_mini_corpora_extractor.py

This script parses a Zotero RDF export folder, extracts metadata and PDF text, and generates:
- A .txt file with all OCR-extracted content using delimiters and page separators
- A metadata .csv file with exported text items
- A full items .csv file with full listing of all items and PDF links


Usage:
    python PhD_mini_corpora_extractor.py /path/to/zotero_export "CorpusName"
"""

import os
import sys
import csv
import fitz  # PyMuPDF
from lxml import etree
from bs4 import BeautifulSoup

# === CONFIGURATION ===
DOC_DELIMITER = "==== BEGIN DOCUMENT {0} ===="
PAGE_SEPARATOR = "[p{0}]"

# === NAMESPACES ===
NS = {
    'rdf': "http://www.w3.org/1999/02/22-rdf-syntax-ns#",
    'z': "http://www.zotero.org/namespaces/export#",
    'dc': "http://purl.org/dc/elements/1.1/",
    'foaf': "http://xmlns.com/foaf/0.1/",
    'bib': "http://purl.org/net/biblio#",
    'dcterms': "http://purl.org/dc/terms/",
    'prism': "http://prismstandard.org/namespaces/1.2/basic/",
    'link': "http://purl.org/rss/1.0/modules/link/"
}

# === PARSING FUNCTIONS ===

def parse_rdf(rdf_path, base_dir):
    tree = etree.parse(rdf_path)
    root = tree.getroot()

    # Parse attachments
    attachments = {}
    for a in root.findall(".//z:Attachment", namespaces=NS):
        a_id = a.attrib['{http://www.w3.org/1999/02/22-rdf-syntax-ns#}about']
        pdf_path = a.find(".//rdf:resource", namespaces=NS)
        pdf_title = a.find(".//dc:title", namespaces=NS)
        if pdf_path is not None:
            path = os.path.join(base_dir, pdf_path.attrib['{http://www.w3.org/1999/02/22-rdf-syntax-ns#}resource'])
            title_pdf = pdf_title.text if pdf_title is not None else ""
            #attachments[a_id] = path   #original
            attachments[a_id] = {
                "path": path,
                "title": title_pdf
            }# This is the file name


    # Parse items
    items = []
    for elem in root:
        if elem.tag.endswith("Memo") or elem.tag.endswith("Journal"):
            continue
        if not elem.tag.startswith("{http://purl.org/net/biblio#}"):
            continue
        item_id = elem.attrib.get('{http://www.w3.org/1999/02/22-rdf-syntax-ns#}about')
        date_elem = elem.find("dc:date", namespaces=NS)
        #journal_elem = elem.find(".//bib:Journal/dc:title", namespaces=NS)   #bellow, there is an ehanced retrieval for journal titles
        link_elems = elem.findall("link:link", namespaces=NS)
        #author_elem = elem.find(".//foaf:surname", namespaces=NS)       #this code only retrieves the last name. The following code concatenates first and last names
        subject_elems = elem.findall("dc:subject", namespaces=NS)
        tags = [s.text.strip() for s in subject_elems if s.text]

        #title has to exclude journals
        title_elem = elem.find("dc:title", namespaces=NS)
        title = title_elem.text.strip() if title_elem is not None else ""

        # Full author list
        persons_authors = elem.findall(".//bib:authors//foaf:Person", namespaces=NS)
        authors = []
        for p in persons_authors:
            given_elem = p.find("foaf:givenName", namespaces=NS)
            surname_elem = p.find("foaf:surname", namespaces=NS)
            given = given_elem.text.strip() if given_elem is not None and given_elem.text else ""
            surname = surname_elem.text.strip() if surname_elem is not None and surname_elem.text else ""
            full_name = f"{given} {surname}".strip()
            if full_name:
                authors.append(full_name)
        author_elem = ", ".join(authors)

        # Full editor list
        persons_editors = elem.findall(".//bib:editors//foaf:Person", namespaces=NS)
        editors = []
        for p in persons_editors:
            given_elem = p.find("foaf:givenName", namespaces=NS)
            surname_elem = p.find("foaf:surname", namespaces=NS)
            given = given_elem.text.strip() if given_elem is not None and given_elem.text else ""
            surname = surname_elem.text.strip() if surname_elem is not None and surname_elem.text else ""
            full_name = f"{given} {surname}".strip()
            if full_name:
                editors.append(full_name)
        editor_elem = ", ".join(editors)

        #Pubication title extraction
        # Step 1: Try to get a direct dc:title inside <dcterms:isPartOf>
        is_part_of_elem = elem.find("dcterms:isPartOf", namespaces=NS)
        publication_title = ""

        if is_part_of_elem is not None:
            inline_title = is_part_of_elem.find(".//dc:title", namespaces=NS)
            if inline_title is not None:
                publication_title = inline_title.text
            else:
                # Step 2: If there's an rdf:resource attribute, look for a matching adjacent element
                ref = is_part_of_elem.attrib.get(f"{{{NS['rdf']}}}resource")
                if ref:
                    # Look for any adjacent bib:* element with matching rdf:about
                    for adj in root:
                        if adj.tag.startswith(f"{{{NS['bib']}}}") and adj.attrib.get(f"{{{NS['rdf']}}}about") == ref:
                            title_elem = adj.find("dc:title", namespaces=NS)
                            if title_elem is not None:
                                publication_title = title_elem.text
                                break



        item = {
            "id": item_id,
            "title": title,
            "author": author_elem,
            "editor": editor_elem,
            "date": date_elem.text if date_elem is not None else "",
            "publication": publication_title,
            "pdfs": [],
            "tags": tags

        }

        for link in link_elems:
            ref = link.attrib['{http://www.w3.org/1999/02/22-rdf-syntax-ns#}resource']
            if ref in attachments:
                item["pdfs"].append(attachments[ref])
        item["pdfs"] = []

        for link in link_elems:
            ref = link.attrib['{http://www.w3.org/1999/02/22-rdf-syntax-ns#}resource']
            if ref in attachments:
                item["pdfs"].append({
                    "path": attachments[ref]["path"],
                    "title": attachments[ref]["title"]
                })


        items.append(item)
    return items

# === PDF TEXT EXTRACTION ===

def extract_text_from_pdfs(items, page_separator=PAGE_SEPARATOR, check_conflict=True):
    extracted_docs = []
    separator_conflict = False

    for doc_index, item in enumerate(items, start=1):
        for pdf in item['pdfs']:
            pdf_path = pdf["path"]
            if not os.path.isfile(pdf_path):
                continue
            try:
                doc = fitz.open(pdf_path)
                pages_text = []
                for i, page in enumerate(doc):
                    text = page.get_text()
                    if check_conflict and page_separator.format(i + 1) in text:
                        separator_conflict = True
                    pages_text.append(f"{page_separator.format(i + 1)}\n{text.strip()}")
                full_text = "\n".join(pages_text).strip()
                extracted_docs.append({
                    "doc_num": doc_index,
                    "metadata": item,
                    "text": full_text
                })
            except Exception as e:
                print(f"Failed to read PDF: {pdf_path}. Error: {e}")
    return extracted_docs, separator_conflict

# === OUTPUT WRITING ===

def write_outputs(extracted_docs, output_dir, corpus_name, page_separator, doc_delimiter, items):
    os.makedirs(output_dir, exist_ok=True)

    header = f"""Corpus name: {corpus_name}
Total number of articles in corpus: {len(items)}
Number of articles in this file: {len(extracted_docs)}
Document delimiter used: {doc_delimiter}
Page separator used: {page_separator}

This file contains multiple OCR-extracted articles (secondary bibliography from historical and related disciplines)
or other documents (primary sources mainly produced between 1850 and 1920).
Each document starts with a "=DOCUMENT METADATA=" delimiter, which contains automatically inserted metadata refering to its title, author, editor, date, publication, and pdf file (title and RDF export path) 
Each document's extracted OCR text is inserted after the "=DOCUMENT TEXT=" delimiter 
When necessary, instructions on how to understand the document (structure, content, etc.) might be added manually, under the "Optional explanation (filled manually)" section.

In some cases (which are usually exceptional):
-Documents might be poorly OCRised or even empty. 
-Documents might contain external elements which are not part of their textual content (in the case of HTML extractions for example)
-Document content might not correspond to the actual refered document (partial, table of contents of a book, reviews, etc.)
Main languages are Spanish, French and English

Corpus description and explanation (filled manually) : EMPTY 

"""
    full_text = [header]
    doc_counter = 1
    for doc in extracted_docs:
        full_text.append(f"{doc_delimiter.format(doc_counter)}")
        full_text.append(f"=DOCUMENT METADATA=")
        full_text.append(f"Document Number: {doc['doc_num']}")  # Incremental number added manually
        doc_counter += 1

        meta = doc["metadata"]  # ← retrieve nested metadata dictionary
        full_text.append(f"Title: {meta.get('title', 'N/A')}")
        full_text.append(f"Author: {meta.get('author', 'N/A')}")
        full_text.append(f"Editor: {meta.get('editor', 'N/A')}")
        full_text.append(f"Date: {meta.get('date', 'N/A')}")
        full_text.append(f"Publication: {meta.get('publication', 'N/A')}")
        full_text.append(f"Link or ID: {meta.get('id', 'N/A')}")

        full_text.append("")  # Blank line
        pdf_titles = "; ".join(pdf["title"] for pdf in meta.get("pdfs", []))
        full_text.append(f"PDF Title: {pdf_titles if pdf_titles else 'N/A'}")
        pdf_paths = "; ".join(pdf["path"] for pdf in meta.get("pdfs", []))
        full_text.append(f"RDF export path: {pdf_paths if pdf_paths else 'N/A'}")

        full_text.append("")  # Blank line before description
        full_text.append(f"Zotero tags: {' ; '.join(meta.get('tags', []))}")
        full_text.append(f"Optional explanation (filled manually): EMPTY")

        full_text.append("")  # Blank line before text
        full_text.append("=DOCUMENT TEXT=")  # Blank line before text
        full_text.append(doc["text"])
        full_text.append("")
    full_output = "\n".join(full_text).strip()
    output_path = os.path.join(output_dir, f"{corpus_name}.txt")

    script_dir = os.path.dirname(os.path.abspath(__file__))
    output_dir = os.path.join(script_dir, "Mini corpora", corpus_name)
    os.makedirs(output_dir, exist_ok=True)

    output_path = os.path.join(output_dir, f"{corpus_name}.txt")
    print("Output folder:", output_dir)



    if len(full_output.encode("utf-8")) > 20 * 1024 * 1024:
        parts = []
        current = []
        size = 0
        part_num = 1
        for line in full_text:
            line_size = len(line.encode("utf-8")) + 1
            if size + line_size > 20 * 1024 * 1024:
                parts.append((part_num, current))
                current = []
                size = 0
                part_num += 1
            current.append(line)
            size += line_size
        if current:
            parts.append((part_num, current))
        for part_num, content in parts:
            with open(os.path.join(output_dir, f"{corpus_name}_part{part_num}.txt"), "w", encoding="utf-8") as f:
                f.write("\n".join(content))
    else:
        with open(output_path, "w", encoding="utf-8") as f:
            f.write(full_output)

    csv_path = os.path.join(output_dir, f"{corpus_name}_text_extracted_items.csv")
    with open(csv_path, "w", encoding="utf-8-sig", newline="") as csvfile:
        writer = csv.writer(csvfile)
        writer.writerow(["Link or ID", "Title", "Author", "Editor", "Date", "Publication", "Has PDF", "PDF titles", "PDF Paths", "Tags"]),
        for doc in extracted_docs:
            meta = doc["metadata"]
            writer.writerow([
                meta["id"],
                meta["title"],
                meta["author"],
                meta["editor"],
                meta["date"],
                meta["publication"],
                "yes" if meta["pdfs"] else "no",
                "; ".join(pdf["path"] for pdf in meta["pdfs"]),
                "; ".join(pdf["title"] for pdf in meta["pdfs"]),
                "; ".join(meta.get("tags", []))
            ])

    # === CSV EXPORT: ALL RDF ITEMS ===
    all_items_csv_path = os.path.join(output_dir, f"{corpus_name}_entire_collection.csv")
    with open(all_items_csv_path, "w", encoding="utf-8-sig", newline="") as csvfile:
        writer = csv.writer(csvfile)
        writer.writerow(["Link or ID", "Title", "Author", "Editor", "Date", "Publication", "Has Attachment", "Included in Corpus", "Attachment title", "Path", "Tags"])
        for item in items:
            item_id = item["id"]
            included_in_corpus = "yes" if any(doc["metadata"]["id"] == item_id for doc in extracted_docs) else "no"
            has_attachment = "yes" if item["pdfs"] else "no"
            pdf_paths = "; ".join([p["path"] for p in item["pdfs"]])  # Assuming your PDFs are dicts with 'path'
            pdf_titles = "; ".join([p["title"] for p in item["pdfs"]])
            tags = "; ".join(item.get("tags", []))
            writer.writerow([
                item_id,
                item.get("title", ""),
                item.get("author", ""),
                item.get("editor", ""),
                item.get("date", ""),
                item.get("publication", ""),
                has_attachment,
                included_in_corpus,
                pdf_titles,
                pdf_paths,
                tags
            ])


# === MAIN FUNCTION ===

def main():
    if len(sys.argv) != 3:
        print("Usage: python PhD_mini_corpora_extractor.py /path/to/folder 'CorpusName'")
        sys.exit(1)

    input_folder = sys.argv[1]
    corpus_name = sys.argv[2]

    rdf_file = None
    for f in os.listdir(input_folder):
        if f.lower().endswith(".rdf"):
            rdf_file = os.path.join(input_folder, f)
            break
    if not rdf_file:
        print("No RDF file found in folder.")
        sys.exit(1)

    items = parse_rdf(rdf_file, input_folder)
    extracted_docs, conflict = extract_text_from_pdfs(items)
    write_outputs(extracted_docs, os.path.join(input_folder, corpus_name), corpus_name, PAGE_SEPARATOR, DOC_DELIMITER, items)

    print("DONE. Output written to the folder")
if __name__ == "__main__":
    main()
