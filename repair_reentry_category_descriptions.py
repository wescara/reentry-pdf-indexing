#!/usr/bin/env python3
"""
Audit and repair missing Reentry top-section category descriptions in Knack.

What it does:
- Reads Reentry category records from Knack (object_13)
- Detects categories whose description field is blank (field_165)
- Builds short descriptions from:
  1) Curated category overrides for known folders
  2) Fallback text synthesized from search_index_full.csv content
- Updates missing descriptions only

Usage:
  python repair_reentry_category_descriptions.py
  python repair_reentry_category_descriptions.py --dry-run
"""

import argparse
import csv
import logging
import os
import re
from collections import defaultdict
from typing import Dict, List

import requests
from dotenv import load_dotenv

load_dotenv(".env")

logging.basicConfig(level=logging.INFO, format="%(asctime)s | %(levelname)s | %(message)s")
logger = logging.getLogger(__name__)

REENTRY_TABLE_ID = "object_13"
FIELD_CATEGORY_NAME = "field_162"
FIELD_DESCRIPTION = "field_165"
SEARCH_INDEX_FULL = "search_index_full.csv"

CATEGORY_DESCRIPTION_OVERRIDES: Dict[str, str] = {
    "Courts (How to Navigate Court Cases)": (
        "Guides for court navigation, case lookups, hearings, and outreach court options to help resolve legal issues after release."
    ),
    "Courts (Navigating Court Cases)": (
        "Guides for court navigation, case lookups, hearings, and outreach court options to help resolve legal issues after release."
    ),
    "Community Resource Lists": (
        "County-based resource guides with links, phone numbers, and service details for housing, food, health, legal, and support needs."
    ),
    "TAM - Medicaid": (
        "Medicaid and TAM-focused resources covering eligibility, enrollment, and access to treatment, prescriptions, and ongoing care."
    ),
    "Veteran Resources": (
        "Veteran-specific support for benefits, healthcare, housing, employment, and reentry services for those with military backgrounds."
    ),
    "Mental Health": (
        "Crisis support, behavioral health services, and counseling resources for those experiencing mental health challenges after release."
    ),
    "Metro Jail Branch Info": (
        "Information about church branch organization and ministering assignments at the Metro Jail facility."
    ),
    "Transportation": (
        "Resources to help recently released individuals find reliable transportation, including rides after jail release."
    ),
    "West Valley City Transition Branches": (
        "Details on the West Valley City transition branches that support members returning from incarceration."
    ),
}

STOPWORDS = {
    "the", "and", "for", "with", "from", "guide", "resources", "resource", "list",
    "hyperlink", "pdf", "oxbow", "branch", "reentry", "how", "use", "to", "of", "in",
}


def require_env(name: str) -> str:
    value = os.getenv(name, "").strip()
    if not value:
        raise RuntimeError(f"Missing required environment value: {name}")
    return value


def first_env(*names: str) -> str:
    for name in names:
        value = os.getenv(name, "").strip()
        if value:
            return value
    raise RuntimeError(f"Missing required environment value: one of {', '.join(names)}")


def knack_headers() -> Dict[str, str]:
    return {
        "X-Knack-Application-Id": first_env("KNACK_APP_ID", "KNACK_APPLICATION_ID"),
        "X-Knack-REST-API-Key": first_env("KNACK_API_KEY", "KNACK_REST_API_KEY"),
        "Content-Type": "application/json",
    }


def knack_api_base() -> str:
    return (os.getenv("KNACK_API_URL") or "https://api.knack.com/v1").rstrip("/")


def get_reentry_records() -> List[Dict]:
    base = knack_api_base()
    endpoint = f"{base}/objects/{REENTRY_TABLE_ID}/records?rows_per_page=100&format=json"
    response = requests.get(endpoint, headers=knack_headers(), timeout=30)
    response.raise_for_status()
    return response.json().get("records", [])


def update_description(record_id: str, description: str) -> None:
    base = knack_api_base()
    endpoint = f"{base}/objects/{REENTRY_TABLE_ID}/records/{record_id}"
    payload = {FIELD_DESCRIPTION: description}
    response = requests.put(endpoint, headers=knack_headers(), json=payload, timeout=30)
    response.raise_for_status()


def _clean_text(value: str) -> str:
    value = re.sub(r"\s+", " ", value or "").strip()
    return value


def _keywords_from_filenames(filenames: List[str], limit: int = 3) -> List[str]:
    candidates: List[str] = []
    for filename in filenames:
        stem = os.path.splitext(filename)[0]
        stem = re.sub(r"[_\-–—]+", " ", stem)
        stem = re.sub(r"\([^)]*\)", " ", stem)
        words = [w.lower() for w in re.findall(r"[A-Za-z0-9']+", stem)]
        words = [w for w in words if len(w) > 2 and w not in STOPWORDS and not w.isdigit()]
        if words:
            phrase = " ".join(words[:4])
            candidates.append(phrase)
    seen = set()
    result = []
    for phrase in candidates:
        if phrase not in seen:
            seen.add(phrase)
            result.append(phrase)
        if len(result) >= limit:
            break
    return result


def build_category_context_from_csv() -> Dict[str, Dict[str, List[str]]]:
    contexts: Dict[str, Dict[str, List[str]]] = defaultdict(lambda: {"filenames": [], "snippets": []})

    if not os.path.exists(SEARCH_INDEX_FULL):
        logger.warning("%s not found, using override/generic descriptions only.", SEARCH_INDEX_FULL)
        return contexts

    with open(SEARCH_INDEX_FULL, "r", encoding="utf-8", errors="replace") as handle:
        reader = csv.DictReader(handle)
        for row in reader:
            category = _clean_text(row.get("parent_category", ""))
            if not category:
                continue
            filename = _clean_text(row.get("filename", ""))
            snippet = _clean_text(row.get("extracted_text_preview", "") or row.get("extracted_text", ""))
            if filename and len(contexts[category]["filenames"]) < 8:
                contexts[category]["filenames"].append(filename)
            if snippet and len(contexts[category]["snippets"]) < 3:
                contexts[category]["snippets"].append(snippet[:400])

    return contexts


def generate_fallback_description(category: str, context: Dict[str, List[str]]) -> str:
    snippets = " ".join(context.get("snippets", [])).lower()
    filenames = context.get("filenames", [])

    keyword_rules = [
        (
            ["court", "judge", "xchange", "warrant", "probation", "hearing"],
            "Step-by-step court resources for case lookup, hearings, warrants, and legal follow-up after release.",
        ),
        (
            ["medicaid", "tam", "coverage", "eligibility", "insurance"],
            "Healthcare access guidance focused on Medicaid/TAM eligibility, enrollment, and treatment support.",
        ),
        (
            ["veteran", "va", "military", "service"],
            "Support resources for veterans, including benefits, healthcare, housing, and employment pathways.",
        ),
        (
            ["community", "county", "shelter", "housing", "food", "resource guide"],
            "Broad community resource listings by county, with contacts and links for housing, food, health, and support services.",
        ),
    ]

    for keywords, description in keyword_rules:
        if any(keyword in snippets for keyword in keywords):
            return description

    filename_keywords = _keywords_from_filenames(filenames)
    if filename_keywords:
        keyword_text = ", ".join(filename_keywords[:3])
        return (
            f"Curated reentry resources for {category}, including guidance on {keyword_text} and related support services."
        )

    return f"Curated reentry resources for {category}, with practical links, contacts, and guidance for successful transition."


def choose_description(category: str, context: Dict[str, List[str]]) -> str:
    if category in CATEGORY_DESCRIPTION_OVERRIDES:
        return CATEGORY_DESCRIPTION_OVERRIDES[category]
    return generate_fallback_description(category, context)


def repair_missing_descriptions(dry_run: bool = False) -> int:
    require_env("TENANT_ID")
    require_env("CLIENT_ID")
    require_env("CLIENT_SECRET")

    records = get_reentry_records()
    contexts = build_category_context_from_csv()

    missing = []
    for record in records:
        category = _clean_text(record.get(FIELD_CATEGORY_NAME, ""))
        description = _clean_text(record.get(FIELD_DESCRIPTION, ""))
        if category and not description:
            missing.append(record)

    logger.info("Reentry categories: %s total, %s missing descriptions", len(records), len(missing))

    updated = 0
    for record in missing:
        record_id = record.get("id", "")
        category = _clean_text(record.get(FIELD_CATEGORY_NAME, ""))
        context = contexts.get(category, {"filenames": [], "snippets": []})
        description = choose_description(category, context)

        if dry_run:
            logger.info("DRY RUN would update %s (%s): %s", record_id, category, description)
            updated += 1
            continue

        update_description(record_id, description)
        logger.info("Updated %s (%s)", record_id, category)
        updated += 1

    logger.info("Done. updated=%s dry_run=%s", updated, dry_run)
    return updated


def main() -> None:
    parser = argparse.ArgumentParser(description="Repair missing Reentry top-section category descriptions in Knack.")
    parser.add_argument("--dry-run", action="store_true", help="Show intended updates without writing to Knack")
    args = parser.parse_args()

    repair_missing_descriptions(dry_run=args.dry_run)


if __name__ == "__main__":
    main()
