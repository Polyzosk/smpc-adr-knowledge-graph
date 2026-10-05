# Εξαγωγή Ανεπιθύμητων Ενεργειών από SmPC σε Γράφο Γνώσης

Κώδικας της πτυχιακής εργασίας «Εξαγωγή και Οργάνωση Ανεπιθύμητων Ενεργειών από SmPC σε Γράφο Γνώσης» (Κωνσταντίνος Πολύζος, ΑΕΜ 4172, Τμήμα Πληροφορικής ΑΠΘ, 2025–2026). Επιβλέπων: Νικόλαος Βασιλειάδης. Συνεπίβλεψη: Αχιλλέας Χύτας.

## Τι κάνει

Κατεβάζει έγγραφα SmPC από το [Electronic Medicines Compendium](https://www.medicines.org.uk/emc), απομονώνει την ενότητα 4.8 («Undesirable effects») και εξάγει με το Google Gemini τις ανεπιθύμητες ενέργειες που αναφέρονται εκεί, μαζί με τη συχνότητα και το SOC τους. Στη συνέχεια τα δεδομένα εμπλουτίζονται με κωδικούς ATC (μέσω RxNorm) και με MedDRA/ICD-10 (μέσω UMLS), και όλα μαζί οργανώνονται σε γράφο γνώσης OWL/RDF σε μορφή Turtle. Ο γράφος επικυρώνεται και αναλύεται με ερωτήματα SPARQL.

Πρόκειται για πιλοτική υλοποίηση σε 50 έγγραφα SmPC, όχι κλινικά επικυρωμένο εργαλείο.

## Αποτελέσματα στο πιλοτικό σύνολο

| Μέγεθος | Τιμή |
|---|---|
| Φάρμακα / έγγραφα SmPC | 50 |
| Μοναδικές ανεπιθύμητες ενέργειες | 1.345 |
| Εμφανίσεις φαρμάκου–ADR | 3.413 |
| Τριπλέτες RDF | 35.939 |
| Κάλυψη ATC | 50/50 (100%) |
| Κάλυψη MedDRA | 1.165/1.345 (86%) |
| Κάλυψη ICD-10 | 698/1.345 (51%) |
| Τιμές SOC | 99 αρχικές → 25 κανονικοποιημένες |

## Δομή

Οι φάκελοι είναι αριθμημένοι με τη σειρά εκτέλεσης. Δεν είναι ενιαίο pipeline που τρέχει με μία εντολή. Κάθε script διαβάζει την έξοδο του προηγούμενου από αρχεία CSV/HTML, έτσι ώστε κάθε στάδιο να μπορεί να ξανατρέξει και να ελεγχθεί μόνο του.

```
01_scraper/            Συλλογή SmPC από το EMC
02_extractor/          Εντοπισμός ενότητας 4.8 και εξαγωγή ADR με Gemini
03_enrichment/         Κανονικοποίηση και εμπλουτισμός με ATC, MedDRA, ICD-10
04_knowledge_graph/    Παραγωγή γράφου, επικύρωση, ερωτήματα SPARQL
data/                  Δεδομένα εισόδου/εξόδου
sparql_results/        Αποτελέσματα των ερωτημάτων SPARQL
```

### 01_scraper

| Script | Ρόλος |
|---|---|
| `get_valid_ids.py` | Εξάγει έγκυρα product IDs από το sitemap του EMC. |
| `html_scraper.py` | Κατεβάζει το HTML κάθε SmPC από το `/smpc/print`, με retries, exponential backoff, καθυστέρηση 6 s και δυνατότητα συνέχισης. |
| `log_stats.py` | Στατιστικά από το `scraping_log.csv`. |
| `scraper.py` | Το πρώτο, δοκιμαστικό scraper (κείμενο για 3 IDs). Δεν χρησιμοποιήθηκε στο τελικό αποτέλεσμα, έμεινε ως ιστορικό. |

**Σημείωση για το `scraping_log.csv`:** το αρχείο είναι σωρευτικό: κάθε εκτέλεση του `html_scraper.py` προσθέτει νέες γραμμές, δεν τις αντικαθιστά. Περιέχει και δοκιμαστικές εκτελέσεις πριν το τελικό, πλήρες run. Τα στατιστικά της πτυχιακής (9.537 SUCCESS, 325 HTTP 404, 10.368 σύνολο) αφορούν αποκλειστικά τις γραμμές από **2026-04-04T01:16:44** και μετά (βλ. `run_metadata.json`). Αν τρέξεις `log_stats.py` χωρίς φίλτρο στο πλήρες log, θα πάρεις διαφορετικά, σωρευτικά νούμερα.

### 02_extractor

| Script | Ρόλος |
|---|---|
| `extract_4_8_blocks.py` | Εντοπίζει την ενότητα 4.8 με regex, από «4.8 Undesirable effects/Adverse reactions» έως «4.9». |
| `pilot_selection.py` | Επιλέγει τυχαία 50 SmPC (seed 42). |
| `extract_drug_names.py` | Εξάγει την εμπορική ονομασία από την ενότητα 1. |
| `gemini_extract_adrs.py` | Εξάγει τριάδες (adr_term, frequency, soc) με το Gemini, temperature 0.0, έξοδος JSON. |
| `audit_4_8_methods.py` | Read-only έλεγχος: ποια μέθοδος χρησιμοποιήθηκε ανά SmPC και αν βρέθηκε το τέλος (4.9). |

### 03_enrichment

| Script | Ρόλος |
|---|---|
| `extract_active_ingredients.py` | Προϊόν → δραστική ουσία (INN) → ATC μέσω RxNav, με 7 χειροκίνητες παρακάμψεις. |
| `umls_enrich_codes.py` | Πρώτο πέρασμα εμπλουτισμού MedDRA/ICD-10 μέσω UMLS. |
| `enrich_meddra_codes.py` | Δεύτερο πέρασμα MedDRA: καθαρισμός όρων, διάσπαση σύνθετων όρων, κανονικοποίηση SOC. |
| `enrich_icd_codes.py` | Δεύτερο πέρασμα ICD-10: MedDRA → CUI → ICD-10, και αναζήτηση ελεύθερου κειμένου. |
| `gemini_extract_active_substance.py` | Πειραματική εξαγωγή INN με Gemini. Δεν χρησιμοποιήθηκε τελικά. |

### 04_knowledge_graph

| Script | Ρόλος |
|---|---|
| `generate_ontology.py` | Παράγει τον γράφο `pilot_knowledge_graph_custom.ttl` με rdflib. |
| `validate_data_quality.py` | Έλεγχοι ακεραιότητας και κάλυψης. |
| `sparql_queries.py` | Εκτελεί 7 ερωτήματα SPARQL, αποθηκεύει τα αποτελέσματα σε CSV. |
| `render_q7_screenshot.py` | Φτιάχνει εικόνα PNG του αποτελέσματος του Q7 (σενάριο ασθενή). |

## Γιατί κάποια βήματα είναι χειροκίνητα

Οι βρετανικές εμπορικές ονομασίες δεν αντιστοιχίζονται αξιόπιστα στο RxNorm/UMLS, οπότε η αντιστοίχιση προϊόν → INN για τα 50 φάρμακα έγινε με το χέρι και επαληθεύτηκε με EMA SmPC, WHO INN και BNF. Σε 7 προϊόντα χρειάστηκε και ο κωδικός ATC να οριστεί χειροκίνητα, από τον WHO ATC/DDD Index.

## Το μοντέλο του γράφου

Namespace `http://example.org/pilot-smpc-kg#`, τέσσερις κλάσεις:

```
:Drug ──hasOccurrence──▶ :DrugAdrOccurrence ──occurrenceOfAdr──▶ :AdverseDrugReaction
                                  │
                                  └──refersToSmPC──▶ :SmPCDocument
```

Η συχνότητα και το SOC είναι στο `:DrugAdrOccurrence`, όχι στην ανεπιθύμητη ενέργεια, γιατί η ίδια ADR μπορεί να έχει διαφορετική συχνότητα σε διαφορετικά φάρμακα. Κάθε εμφάνιση δείχνει και το SmPC από το οποίο προήλθε.

## Εγκατάσταση και εκτέλεση

Python 3.10+.

```bash
pip install -r requirements.txt
cp .env.example .env
```

Στο `.env` συμπληρώνονται το κλειδί για το Gemini API, από το [Google AI Studio](https://aistudio.google.com/), και το κλειδί για το UMLS, από το [UTS](https://uts.nlm.nih.gov/uts/), που θέλει δωρεάν λογαριασμό και αποδοχή άδειας χρήσης. Το RxNav δεν θέλει κλειδί.

Όλα τρέχουν από τον ριζικό φάκελο, με αυτή τη σειρά:

```bash
python 01_scraper/get_valid_ids.py
python 01_scraper/html_scraper.py
python 02_extractor/extract_4_8_blocks.py
python 02_extractor/pilot_selection.py
python 02_extractor/extract_drug_names.py
python 02_extractor/gemini_extract_adrs.py
python 03_enrichment/extract_active_ingredients.py
python 03_enrichment/umls_enrich_codes.py
python 03_enrichment/enrich_meddra_codes.py
python 03_enrichment/enrich_icd_codes.py
python 04_knowledge_graph/generate_ontology.py
python 04_knowledge_graph/validate_data_quality.py
python 04_knowledge_graph/sparql_queries.py
```

Το πλήρες scraping των περίπου 10.000 εγγράφων παίρνει αρκετές ώρες λόγω της καθυστέρησης μεταξύ αιτημάτων. Η εξαγωγή με Gemini περιορίζεται από τα όρια χρήσης του API.

## Δεδομένα

Τα ακατέργαστα HTML των SmPC δεν είναι στο repository, γιατί ανήκουν στους κατόχους άδειας κυκλοφορίας και στο EMC. Μπορούν να ξανακατεβούν με το `html_scraper.py`. Υπάρχουν όμως τα παραγόμενα δεδομένα: τα εμπλουτισμένα CSV, ο τελικός γράφος (`.ttl`) και τα αποτελέσματα SPARQL. Ο φάκελος `data/output/legacy/` έχει παλιά αρχεία από προηγούμενες εκδόσεις, κρατημένα για ιστορικούς λόγους.

## Περιορισμοί

Πιλοτική κλίμακα, 50 έγγραφα. Δεν έγινε έλεγχος precision/recall της εξαγωγής από ειδικό. Οι σύνθετοι όροι ADR δεν αντιστοιχίζονται πάντα σε έναν όρο MedDRA. Η κάλυψη σε ICD-10 είναι χαμηλότερη από το MedDRA επειδή το ICD-10 κωδικοποιεί διαγνώσεις, όχι ανεπιθύμητες ενέργειες. Αυτό είναι αναμενόμενο, όχι σφάλμα.

Αναλυτικά όλα αυτά περιγράφονται στην ίδια την πτυχιακή.
