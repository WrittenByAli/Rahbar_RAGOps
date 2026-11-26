# Evidence for the AI-drafted golden questions

Drafted by an AI agent. **Nothing here has been reviewed by a member.** To review a question, open the
cited page yourself. `page` is the 1-based physical PDF page (HTML and Markdown are page 1).
Quotes are short phrases for locating the answer; line breaks are collapsed to spaces and table
cell separators are dropped. `g` ids are in `golden.jsonl`; `c` ids are optional extras in
`golden_candidates.jsonl`. Check with `python eval/check_golden_pages.py` (needs corpus A in
`corpus/raw/` and the built Batch B files).

## Reading the two handbook PDFs

The rules handbook and the student handbook keep text from a neighbouring page outside the visible
page area on many pages. A plain `extract_text()` returns it too, so one passage can seem to sit on
two pages. Pages here are the page on which the text is **visible**; the checker drops off-page
characters.

## Answerable

| id | doc_id | version | page | evidence |
|---|---|---|---|---|
| g001 | ug-academic-rules | 2026-06 | 10 | `Absence of a maximum of 20% of the` |
| g001 | ug-academic-rules | 2026-06 | 10 | `assigned F/A grade in the course` |
| g002 | ug-academic-rules | 2026-06 | 21 | `and his warning count must be zero to register for` |
| g002 | ug-academic-rules | 2026-06 | 21 | `registration in FYP-I where there is deficiency of two courses` |
| g003 | ug-academic-rules | 2026-06 | 23 | `The Grade in the course to be replaced is` |
| g003 | ug-academic-rules | 2026-06 | 23 | `A core course cannot be replaced by any other course` |
| g004 | ug-academic-rules | 2026-06 | 27 | `within seven days of the announcement of the result` |
| g004 | ug-academic-rules | 2026-06 | 27 | `Rechecking fee will be refunded if there is an increase in` |
| g005 | ug-academic-rules | 2026-06 | 35 | `access to a question paper before the examination` |
| g005 | ug-academic-rules | 2026-06 | 35 | `expulsion from the University` |
| g006 | ug-academic-rules | 2026-06 | 30 | `minimum CGPA required is 3.00 for undergraduate programs` |
| g006 | ug-academic-rules | 2026-06 | 30 | `Agha Hasan Abedi Gold Medal awarded for First` |
| g007 | fee-structure | 2026-27 | 1 | `A refundable security deposit of Rs 20,000` |
| g007 | fee-structure | 2026-27 | 1 | `refundable at the time of completion of degree or closing of admission` |
| g008 | fee-structure | 2026-27 | 1 | `Upto 20th day of commencement of classes` |
| g008 | fee-structure | 2026-27 | 1 | `60% Fee Refund` |
| g009 | grading | 2026 | 1 | `Letter grades for BBA & BS programs` |
| g009 | grading | 2026 | 1 | `B 3.00 Good` |
| g009 | grading | 2026 | 1 | `Letter grades for MBA & MS programs` |
| g009 | grading | 2026 | 1 | `B 3.00 Average` |
| g010 | student-handbook | 2026-06 | 33 | `Undergradu 04 02 02 Weeks PKR PKR` |
| g010 | student-handbook | 2026-06 | 33 | `10/item/day 5,000` |
| g011 | student-handbook | 2026-06 | 25 | `made within one year of` |
| g011 | student-handbook | 2026-06 | 25 | `pursuing studies in the same` |
| g012 | student-handbook | 2026-06 | 38 | `rules would result in a fine of Rs. 500/-` |
| g013 | annual-report-2023-24 | 2023-24 | 13 | `Total 42 37 128 58 325 115 30 735` |
| g014 | annual-report-2019-20 | 2019-20 | 20 | `G. Total 306 132 - - 1048 74 - - 336 - - 1896` |
| g014 | annual-report-2023-24 | 2023-24 | 19 | `G. Total 253 104 132 1889 82 81 68 378 443 3430` |
| g015 | ug-academic-rules | 2026-06 | 22 | `freezing of his/her admission for up to` |
| g015 | ug-academic-rules | 2026-06 | 22 | `two semester of BS degree are not allowed` |
| g016 | student-id-card-replacement | 2026-09 | 1 | `First loss or theft of the card: Rs 800` |
| g017 | fee-structure | 2027-28 | 1 | `For the academic year 2027-28` |
| g017 | fee-structure | 2027-28 | 1 | `BBA / BS Rs. 13,200` |
| g018 | makerspace-equipment-loan-rules | 2026-09 | 1 | `Portable oscilloscopes and soldering stations 3 calendar days Rs 400` |
| g019 | industrial-internship-guidelines | 2026-08 | 1 | `at least six consecutive weeks and at least 180` |
| g019 | industrial-internship-guidelines | 2026-08 | 1 | `Two shorter placements cannot be added together` |
| c001 | ug-academic-rules | 2026-06 | 30 | `SGPA of 4.00 with at least 30 credit hours` |
| c002 | ug-academic-rules | 2026-06 | 23 | `deadline to withdraw from a course is the last day` |
| c003 | student-handbook | 2026-06 | 31 | `group (at- least three members) for one hour` |
| c004 | student-handbook | 2026-06 | 26 | `Mobile Phones, Smart Watches and all electronic` |
| c005 | student-handbook | 2026-06 | 36 | `recommendation of concerned faculty` |
| c006 | annual-report-2019-20 | 2019-20 | 13 | `Faculty 97 170 122 177 2 47 615` |
| c007 | fee-structure | 2026-27 | 1 | `Additional Transcript Fee Rs. 300` |
| c007 | fee-structure | 2026-27 | 1 | `Campus Transfer Fee Rs. 5,000` |
| c008 | annual-report-2019-20 | 2019-20 | 24 | `Lahore 71` |
| c008 | annual-report-2023-24 | 2023-24 | 63 | `Lahore (LHR) 160` |
| c009 | student-locker-allocation | 2026-07 | 1 | `replacement charge is Rs 450` |
| c009 | student-locker-allocation | 2026-07 | 1 | `changed within two working days` |
| c010 | student-cloud-credits | 2026-10 | 1 | `receive up to USD 120 of credit` |
| c010 | student-cloud-credits | 2026-10 | 1 | `when 50 percent of the allocation has been used` |
| c011 | industrial-internship-guidelines | 2026-08 | 1 | `score of at least 60 percent is needed to pass` |
| c011 | industrial-internship-guidelines | 2026-08 | 1 | `does not change the SGPA or the CGPA` |
| c012 | makerspace-equipment-loan-rules | 2026-09 | 1 | `Filament is charged at Rs 12 per gram` |
| c012 | makerspace-equipment-loan-rules | 2026-09 | 1 | `at most six hours in one slot` |

## Unanswerable

Each search pattern (case-insensitive regular expression) had no match in the extractable text of
corpus A or of the Batch B documents. **Not searched:** image-only content. No OCR was run, so all 8
pages of the entrepreneurship policy, nearly all of the 2023 rules, the blank-looking or image pages of
the two handbooks, and any pictures inside other pages were not read. A search with no match is not
proof of absence; a member should also check by reading. In the table, `\|` stands for `|`.

| id | pattern | note |
|---|---|---|
| g020 | `duplicate (degree\|transcript)` | Near miss: the fee page lists degree correction, transcript and certificate fees, but no duplicate-degree fee. |
| g020 | `(degree\|certificate) (replacement\|reissu)` |  |
| g020 | `lost (original )?degree` |  |
| g021 | `(cafeteria\|canteen)[^.]{0,80}?\b(rs\.?\|pkr) ?\d` | Near miss: cafeterias are mentioned; no prices were found. |
| g021 | `(meal\|food) (price\|cost\|charges?)` |  |
| g022 | `convocation (fee\|charges?)` | Convocations are reported; no fee was found. |
| g022 | `(fee\|charges?) for (the )?convocation` |  |
| g023 | `(similarity\|turnitin)[^.]{0,80}?\d+ ?(%\|percent)` | Near miss: Turnitin and plagiarism are discussed, but no threshold was found. The handbooks' Generative AI sections give other percentages about marks. |
| g023 | `\d+ ?(%\|percent)[^.]{0,60}?(similarity\|turnitin\|plagiari)` |  |
| g024 | `(bus\|buses\|coaster\|transport)[^.]{0,80}?\b(rs\.?\|pkr) ?\d` | Near miss: the annual reports say Karachi transport is subsidised self-finance but give no amount. |
| g024 | `(transport\|bus) (fee\|fare\|charges?)` |  |
| g025 | `comprehensive exam` | PhD grading and warning rules exist, but no comprehensive-exam rule was found. |
| g025 | `qualifying exam` |  |
| g025 | `candidacy exam` |  |
| g026 | `stipend[^.]{0,80}?\b(rs\.?\|pkr) ?\d` | Near miss: the scholarship is said to cover a monthly stipend; no amount is given. |
| g026 | `\b(rs\.?\|pkr) ?[\d,]+[^.]{0,60}?stipend` |  |
