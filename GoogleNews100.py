from __future__ import annotations

import time
import re
from typing import List, Tuple, Optional
from datetime import datetime
from urllib.parse import quote

import requests
import feedparser
from bs4 import BeautifulSoup
from readability import Document

# Playwright (fallback quand cookies/consentement)
from playwright.sync_api import sync_playwright


# ----------------------------
# VARIABLES
# ----------------------------
KEYWORDS: List[str] = [
    "pneumologie"
]

OUTPUT_FILE: str = "C:/PYTHON/.data/Google_news_resultat.txt"

RESULTS_PER_KEYWORD: int = 10
REQUEST_TIMEOUT_SEC: int = 20
SLEEP_BETWEEN_REQUESTS_SEC: float = 1.0

# Paramètres Google News (FR/France)
NEWS_HL = "fr"
NEWS_GL = "FR"
NEWS_CEID = "FR:fr"

# Playwright
PLAYWRIGHT_TIMEOUT_MS: int = 20000


# ----------------------------
# OUTILS
# ----------------------------
def clean_text(text: str) -> str:
    text = re.sub(r"\r\n?", "\n", text)
    text = re.sub(r"[ \t]+\n", "\n", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    text = re.sub(r"[ \t]{2,}", " ", text)
    return text.strip()


def is_google_or_consent_url(url: str) -> bool:
    """
    Détecte les URLs Google News / pages de consentement Google.
    """
    u = url.lower()
    return (
        "news.google.com" in u
        or "consent.google" in u
        or "accounts.google.com" in u
        or "google.com/sorry" in u
    )


def looks_like_cookie_consent_page(text_or_html: str) -> bool:
    """
    Détection simple (pas parfaite) des pages cookies/consentement.
    """
    t = (text_or_html or "").lower()
    triggers = [
        "before you continue",
        "we use cookies",
        "cookies and data",
        "consent",
        "privacy",
        "reject all",
        "accept all",
        "tout accepter",
        "tout refuser",
        "j'accepte",
        "accepter",
        "refuser",
        "paramètres de confidentialité",
    ]
    return any(x in t for x in triggers)


# ----------------------------
# GOOGLE NEWS RSS
# ----------------------------
def google_news_rss(query: str) -> feedparser.FeedParserDict:
    q = quote(query)
    rss_url = (
        f"https://news.google.com/rss/search?q={q}"
        f"&hl={NEWS_HL}&gl={NEWS_GL}&ceid={NEWS_CEID}"
    )
    return feedparser.parse(rss_url)


def extract_publisher_url_from_summary(entry: feedparser.FeedParserDict) -> Optional[str]:
    """
    Dans certains flux RSS, le vrai lien éditeur est dans entry.summary (HTML).
    """
    summary = entry.get("summary", "")
    if not summary:
        return None

    soup = BeautifulSoup(summary, "html.parser")
    a = soup.find("a", href=True)
    if a:
        href = a.get("href", "")
        if href.startswith("http") and "news.google.com" not in href:
            return href
    return None


def extract_real_article_url(entry: feedparser.FeedParserDict) -> Optional[str]:
    """
    Stratégie:
    1) entry.source.href
    2) lien dans entry.summary
    3) entry.links rel=alternate (si pas Google)
    4) fallback entry.link
    """
    src = entry.get("source")
    if src:
        href = src.get("href")
        if href and href.startswith("http"):
            return href

    u = extract_publisher_url_from_summary(entry)
    if u:
        return u

    links = entry.get("links", [])
    for l in links:
        href = l.get("href")
        rel = l.get("rel")
        if rel == "alternate" and href and href.startswith("http"):
            if "news.google.com" not in href:
                return href

    link = entry.get("link")
    if link and link.startswith("http"):
        return link

    return None


def search_google_news_urls(query: str, max_results: int = 100) -> List[str]:
    feed = google_news_rss(query)
    urls: List[str] = []

    for entry in feed.entries:
        u = extract_real_article_url(entry)
        if u:
            urls.append(u)
        if len(urls) >= max_results:
            break

    seen = set()
    out = []
    for u in urls:
        if u not in seen:
            seen.add(u)
            out.append(u)

    return out[:max_results]


# ----------------------------
# EXTRACTION PAGE (Readability)
# ----------------------------
def extract_article_html_with_readability(html: str) -> str:
    doc = Document(html)
    return doc.summary(html_partial=True)


def html_to_text(html: str) -> str:
    soup = BeautifulSoup(html, "html.parser")
    for tag in soup(["script", "style", "noscript"]):
        tag.decompose()
    return clean_text(soup.get_text(separator="\n"))


def extract_text_from_html(html: str) -> Tuple[bool, str]:
    """
    (HTML brut) -> (ok, texte)
    """
    try:
        article_html = extract_article_html_with_readability(html)
        text = html_to_text(article_html)
        if not text:
            return False, "[INACCESSIBLE] Contenu vide ou non extractible"
        return True, text
    except Exception as e:
        return False, f"[INACCESSIBLE] Erreur extraction: {e}"


def fetch_html_requests(url: str) -> Tuple[bool, str]:
    """
    Récupère le HTML via requests.
    """
    headers = {
        "User-Agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/120.0 Safari/537.36"
        )
    }

    try:
        resp = requests.get(url, headers=headers, timeout=REQUEST_TIMEOUT_SEC, allow_redirects=True)
        if resp.status_code >= 400:
            return False, f"[INACCESSIBLE] HTTP {resp.status_code}"

        ctype = resp.headers.get("Content-Type", "")
        if "text/html" not in ctype and "application/xhtml+xml" not in ctype:
            return False, f"[INACCESSIBLE] Content-Type non HTML: {ctype}"

        return True, resp.text
    except requests.exceptions.RequestException as e:
        return False, f"[INACCESSIBLE] Erreur réseau: {e}"


def fetch_html_playwright(url: str) -> Tuple[bool, str]:
    """
    Fallback : ouvre la page avec un vrai navigateur (Playwright),
    tente de fermer/valider un popup cookies, puis récupère le HTML.
    """
    try:
        with sync_playwright() as p:
            browser = p.chromium.launch(headless=True)
            page = browser.new_page()

            page.goto(url, timeout=PLAYWRIGHT_TIMEOUT_MS, wait_until="domcontentloaded")

            # Tente de cliquer sur des boutons de consentement fréquents
            button_texts = [
                "Accept all", "Reject all",
                "Tout accepter", "Tout refuser",
                "J'accepte", "Je refuse",
                "Accepter", "Refuser",
                "I agree", "I accept",
                "Agree", "Accept",
            ]

            for txt in button_texts:
                try:
                    page.get_by_role("button", name=txt).click(timeout=1500)
                    time.sleep(1.0)
                    break
                except Exception:
                    pass

            # Parfois ce ne sont pas des <button>, mais des liens ou div cliquables
            # On tente un clic "texte" générique
            fallback_texts = ["Accept", "Accepter", "Tout accepter", "I agree", "J'accepte"]
            for txt in fallback_texts:
                try:
                    page.get_by_text(txt, exact=False).first.click(timeout=1500)
                    time.sleep(1.0)
                    break
                except Exception:
                    pass

            html = page.content()
            browser.close()
            return True, html

    except Exception as e:
        return False, f"[INACCESSIBLE] Playwright: {e}"


def extract_page_text(url: str) -> Tuple[bool, str]:
    """
    1) essaie requests (rapide)
    2) si page cookies/consentement détectée -> fallback playwright (plus lent mais efficace)
    """
    ok_html, html_or_err = fetch_html_requests(url)
    if not ok_html:
        return False, html_or_err

    # Si on détecte une page cookies/consentement, on passe à Playwright
    if looks_like_cookie_consent_page(html_or_err):
        ok_pw, html_pw_or_err = fetch_html_playwright(url)
        if not ok_pw:
            return False, html_pw_or_err
        return extract_text_from_html(html_pw_or_err)

    # Sinon extraction normale
    return extract_text_from_html(html_or_err)


# ----------------------------
# SORTIE
# ----------------------------
def write_results(output_file: str, results: List[Tuple[str, bool, str]]) -> None:
    with open(output_file, "w", encoding="utf-8") as f:
        for url, ok, content in results:
            f.write(url.strip() + "\n")
            f.write(content.strip() + "\n\n")


# ----------------------------
# MAIN
# ----------------------------
def main() -> None:
    print(f"{datetime.now().strftime('%d/%m/%Y %H:%M:%S')} - Début")

    all_results: List[Tuple[str, bool, str]] = []
    already_done = set()

    for kw in KEYWORDS:
        print(f"\n{datetime.now().strftime('%d/%m/%Y %H:%M:%S')} - Recherche Google Actualités: {kw}")
        urls = search_google_news_urls(kw, RESULTS_PER_KEYWORD)
        print(f"{datetime.now().strftime('%d/%m/%Y %H:%M:%S')} - URLs récupérées: {len(urls)}")

        for i, url in enumerate(urls, start=1):
            if url in already_done:
                continue
            already_done.add(url)

            # Ignore liens Google/consentement
            if is_google_or_consent_url(url):
                print(f"{datetime.now().strftime('%d/%m/%Y %H:%M:%S')} - Ignoré (Google/consentement): {url}")
                all_results.append((url, False, "[INACCESSIBLE] Lien Google/consentement ignoré"))
                continue

            print(f"{datetime.now().strftime('%d/%m/%Y %H:%M:%S')} - [{i}/{len(urls)}] Ouvre: {url}")
            ok, content = extract_page_text(url)
            all_results.append((url, ok, content))

            time.sleep(SLEEP_BETWEEN_REQUESTS_SEC)

    write_results(OUTPUT_FILE, all_results)
    print(f"\n{datetime.now().strftime('%d/%m/%Y %H:%M:%S')} - Fichier créé: {OUTPUT_FILE}")
    print(f"{datetime.now().strftime('%d/%m/%Y %H:%M:%S')} - Pages traitées: {len(all_results)}")
    print(f"{datetime.now().strftime('%d/%m/%Y %H:%M:%S')} - Fin")


if __name__ == "__main__":
    main()
