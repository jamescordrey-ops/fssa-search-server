# g0002 candidate — binary-text corpus repair delta

Built 2026-10-14 (EDT) on the VM, in ~/workspace/fssa-qa/v22.2/.
No existing corpus or generation was mutated: this directory is a
standalone delta. At the v22.2 deploy, the generation rebuild swaps
each repaired doc's `text` (and `title`, where extraction recovered a
real one) into the corpus by URL, rebuilds the generation, and the PC
re-embeds the changed chunks.

## What was wrong

52 documents in corpus_federal_v19.json (none in corpus_v19.json) held
raw file/response bytes as their stored text, decoded byte-for-byte
(latin-1): 22 medicare.gov publication files (15 EPUB, 6 PDF, 1 MOBI)
stored as truncated prefixes of the raw files, and 30 ssa.gov /
choosework.ssa.gov / yourtickettowork.ssa.gov pages whose stored text
is a raw (in most captures gzip-compressed) response body. These are
the docs behind the ~53 binary sections the v22 enrichment writers
flagged in g0001 (doc-level count here: 52; the generation builder's
sectioner splits one of them into two sections).

## Repair routes

The downloaded originals survive locally as crawl snapshots
(crawl-federal-2026-10-12/ named by sha1(url); crawl-ssa-2026-10-12/
named by md5(url)[:16]; the SSA captures are Wayback snapshots and
the file bodies are gzip-compressed). 36 of 52 matched a snapshot by
exact URL hash; the other 16 matched a same-path manifest variant
(query-string differences only). Extraction QA per doc: length,
readability (ASCII or native-script character evidence), and a
404/boilerplate screen. Two first-pass 'repairs' were REJECTED by
that screen because their captures are Wayback 404 pages; one of
them (choosework /about/) was then recovered by the single allowed
re-download, the other (yourtickettowork about page) is gone (the
live fetch returns 404) and stays remains-binary.

## Result: 48 repaired, 4 remains-binary, of 52 attempted

Repaired docs: 110,116 chars of stored garbage replaced by 2,062,588
chars of readable text; embedding chunks for these docs go
251 -> 4,151 (server `_emb_chunk_count` replica; the generation
rebuild at deploy re-derives sections authoritatively - under the
server's `_split_sections` each repaired doc measures 1 section
before and after, as does the garbage it replaces).

Files: `repaired_docs.json` (replacement docs in corpus shape:
{title, url, source, verified, text}), `manifest.json` (per-doc
record incl. snapshot file, method, old/new lengths, section/chunk
counts), `remains_binary.json` (the 4 unrecovered docs + reasons).

## Repaired documents

| # | URL | Old chars | New chars | Chunks old->new | Method |
|---|---|---|---|---|---|
| 1 | https://www.medicare.gov/publications/10988-medicare-coverage-of-outpatient-occupational-therapy-physical-therapy-and-speech-language | 634 | 3231 | 2->7 | PDF pdftotext (poppler) |
| 2 | https://www.medicare.gov/publications/11408-s-medicare-your-hospital-benefits.pdf-0 | 2162 | 7807 | 5->16 | PDF pdftotext (poppler) |
| 3 | https://www.medicare.gov/publications/10050-medicare-and-you..epub | 2238 | 289720 | 5->580 | EPUB spine (zipfile+lxml) |
| 4 | https://www.medicare.gov/publications/10116-your-medicare-benefits.epub | 1820 | 197694 | 4->396 | EPUB spine (zipfile+lxml) |
| 5 | https://www.medicare.gov/publications/10128-medicare-coverage-of-kidney-dialysis.epub | 3778 | 67176 | 8->135 | EPUB spine (zipfile+lxml) |
| 6 | https://www.medicare.gov/publications/10153-medicare-coverage-of-skilled-nursing-facility-care.epub | 4426 | 43394 | 9->87 | EPUB spine (zipfile+lxml) |
| 7 | https://www.medicare.gov/publications/11036_enrolling_in_medicare_part_a_and_part_b.epub | 1604 | 49042 | 4->99 | EPUB spine (zipfile+lxml) |
| 8 | https://www.medicare.gov/publications/11219-understanding-medicare-part-c.epub | 2066 | 17455 | 5->35 | EPUB spine (zipfile+lxml) |
| 9 | https://www.medicare.gov/publications/11306-what-is-medicare-what-is-medicaid.epub | 4632 | 4269 | 10->9 | EPUB spine (zipfile+lxml) |
| 10 | https://www.medicare.gov/publications/11109-your-guide-to-medicare-prescription-drug-coverage.epub | 2581 | 90184 | 6->181 | EPUB spine (zipfile+lxml) |
| 11 | https://www.medicare.gov/publications/11525-medicare-appeals.epub | 1403 | 91306 | 3->183 | EPUB spine (zipfile+lxml) |
| 12 | https://www.medicare.gov/publications/medicare_and_other_health_benef_-_centers_for_medicare.epub | 1038 | 48941 | 3->98 | EPUB spine (zipfile+lxml) |
| 13 | https://www.medicare.gov/publications/your_guide_to_medicares_preventive_services.epub | 4237 | 34709 | 9->70 | EPUB spine (zipfile+lxml) |
| 14 | https://www.medicare.gov/publications/02174-nursing-home-other-long-term-services.epub | 625 | 17679 | 2->36 | EPUB spine (zipfile+lxml) |
| 15 | https://www.medicare.gov/publications/10110-Your-Guide-to-Medicare-Preventive-Services.epub | 1056 | 41070 | 3->83 | EPUB spine (zipfile+lxml) |
| 16 | https://www.medicare.gov/publications/11359-Medicare-Skilled-Nursing-Facility-Care-Getting-Started.epub | 2199 | 58190 | 5->117 | EPUB spine (zipfile+lxml) |
| 17 | https://www.medicare.gov/publications/12085-opioid-treatment-programs.pdf-0 | 628 | 2781 | 2->6 | PDF pdftotext (poppler) |
| 18 | https://www.medicare.gov/publications/11220-ilokano-philippines.pdf-0 | 5283 | 6346 | 11->13 | PDF pdftotext (poppler) |
| 19 | https://www.medicare.gov/publications/11220-gujarati.pdf-0 | 1910 | 5592 | 4->12 | PDF pdftotext (poppler) |
| 20 | https://www.medicare.gov/publications/11220-amharic-ethiopia-.pdf-0 | 4283 | 4027 | 9->9 | PDF pdftotext (poppler) |
| 21 | https://www.medicare.gov/publications/10050-s-medicare-and-you.epub | 2610 | 302106 | 6->605 | EPUB spine (zipfile+lxml) |
| 22 | https://www.ssa.gov/payee/reviews_by_Protection_and_Advocacy.htm | 3050 | 4680 | 7->10 | HTML parse_ssa_crawl.extract (gzip snapshot) |
| 23 | https://choosework.ssa.gov/library/larry-success-story-update | 2112 | 9272 | 5->19 | HTML parse_ssa_crawl.extract (gzip snapshot) |
| 24 | https://choosework.ssa.gov/about/index.html&quot | 4677 | 2745 | 10->6 | single re-download + lxml |
| 25 | https://www.ssa.gov/ssi/wage-reporting.html | 1057 | 2564 | 3->6 | HTML parse_ssa_crawl.extract (gzip snapshot) |
| 26 | https://www.ssa.gov/benefits/survivors/ifyou.html | 2557 | 12375 | 6->25 | HTML parse_ssa_crawl.extract (gzip snapshot) |
| 27 | https://www.ssa.gov/benefits/retirement/planner/taxes.html | 966 | 2408 | 2->5 | HTML parse_ssa_crawl.extract (gzip snapshot) |
| 28 | https://www.ssa.gov/payee/elder_abuse.htm | 740 | 1068 | 2->3 | HTML parse_ssa_crawl.extract (gzip snapshot) |
| 29 | https://www.ssa.gov/disability/amount | 579 | 1827 | 2->4 | HTML <main> (gzip snapshot) |
| 30 | https://www.ssa.gov/disability/reporting | 3569 | 784 | 8->2 | HTML <main> (gzip snapshot) |
| 31 | https://www.ssa.gov/disability/restart | 546 | 1158 | 2->3 | HTML <main> (gzip snapshot) |
| 32 | https://www.ssa.gov/disability/work | 2528 | 2834 | 6->6 | HTML <main> (gzip snapshot) |
| 33 | https://www.ssa.gov/ssi/get-more-help | 4057 | 1473 | 9->3 | HTML <main> (gzip snapshot) |
| 34 | https://choosework.ssa.gov/library/tags/ticket-to-work-program.html | 1553 | 606 | 4->2 | HTML parse_ssa_crawl.extract (gzip snapshot) |
| 35 | https://choosework.ssa.gov/library/tags/ticket-program.html | 1535 | 1023 | 4->3 | HTML parse_ssa_crawl.extract (gzip snapshot) |
| 36 | https://choosework.ssa.gov/library/tags/success-job | 610 | 1518 | 2->4 | HTML parse_ssa_crawl.extract (gzip snapshot) |
| 37 | https://www.ssa.gov/appeals/inperson.html | 2618 | 6296 | 6->13 | HTML parse_ssa_crawl.extract (gzip snapshot) |
| 38 | https://www.ssa.gov/benefits/retirement/ssfa-zip-code.html | 4102 | 462434 | 9->925 | HTML parse_ssa_crawl.extract (gzip snapshot) |
| 39 | https://www.ssa.gov/payee//faqrep.htm | 1565 | 22316 | 4->45 | HTML parse_ssa_crawl.extract (gzip snapshot) |
| 40 | https://choosework.ssa.gov/library/fact-sheet-plan-to-achieve-self-support | 971 | 5754 | 2->12 | HTML parse_ssa_crawl.extract (gzip snapshot) |
| 41 | https://www.ssa.gov/ssi/text-overpay-ussi.htm | 5162 | 4336 | 11->9 | HTML parse_ssa_crawl.extract (gzip snapshot) |
| 42 | https://www.ssa.gov/benefits/retirement/planner/delayret.html | 765 | 2513 | 2->6 | HTML parse_ssa_crawl.extract (gzip snapshot) |
| 43 | https://www.ssa.gov/payee/bene.htm | 972 | 6522 | 2->14 | HTML parse_ssa_crawl.extract (gzip snapshot) |
| 44 | https://www.ssa.gov/payee/NewGuide/toc.htm | 1096 | 90092 | 3->181 | HTML parse_ssa_crawl.extract (gzip snapshot) |
| 45 | https://www.ssa.gov/ssi/text-work-ussi.htm | 2998 | 18377 | 6->37 | HTML parse_ssa_crawl.extract (gzip snapshot) |
| 46 | https://www.ssa.gov/redbook/newfor2023.htm | 5200 | 2069 | 11->5 | HTML parse_ssa_crawl.extract (gzip snapshot) |
| 47 | https://www.ssa.gov/redbook/eng/demoprojects.htm | 1708 | 7376 | 4->15 | HTML parse_ssa_crawl.extract (gzip snapshot) |
| 48 | https://www.ssa.gov/redbook/eng/definedisability.htm | 1610 | 5449 | 4->11 | HTML parse_ssa_crawl.extract (gzip snapshot) |

## Remains binary (unrecoverable)

- https://www.medicare.gov/publications/11306_what_is_medicare_what_is_medicaid.mobi — MOBI is PalmDOC-compressed (compression=2) and no MOBI extractor exists on this VM (stdlib/PyMuPDF absent); not substituted. The identical publication (Medicare publication 11306) IS repaired in this delta via its EPUB twin doc
- https://choosework.ssa.gov/perc-common-ui/cors/ — not a content page: the URL is a cross-domain iframe shim (perc-common-ui/cors); the local capture extracts to 45 chars of shim text and no readable document exists at this URL
- https://yourtickettowork.ssa.gov/web/ttw/en-about-ticket-to-work — local original unusable (see record); single fetch of https://yourtickettowork.ssa.gov/web/ttw/en-about-ticket-to-work failed: HTTPError: HTTP Error 404: Not Found
- https://www.ssa.gov/redbook/eng/nav.html — local original unusable (see record); single fetch of https://www.ssa.gov/redbook/eng/nav.html failed: HTTPError: HTTP Error 403: Forbidden

## Observed, out of scope (not in this delta)

Five choosework.ssa.gov pages (the success-story pages +
meet-your-employment-team) are NOT binary: their text is readable
UTF-8 misdecoded as latin-1 (curly punctuation shows as mojibake,
e.g. 'Kushalâ€™s'). Content is present and searchable; a decode
fix is a separate, smaller repair than re-extraction and was not
folded into this binary-scope delta.
