"""
But : pour une liste de mots-clés,
- récupérer les 100 premiers résultats Google (via l’API officielle Google Custom Search),
- ouvrir chaque page,
- extraire le texte,
- signaler si la page est inaccessible,
- écrire un seul fichier texte au format :
  lien
  contenu
  lien
  contenu
  etc.

"""
# ----------------------------
# BIBLIOTHEQUES
# ----------------------------
from __future__ import annotations

import time
import re
from typing import List, Dict, Tuple, Optional

import requests
from bs4 import BeautifulSoup
from datetime import datetime

# ----------------------------
# VARIABLES À REMPLIR
# ----------------------------
KEYWORDS: List[str] = [
    "pneumologie",
    "pneumologue",
]

OUTPUT_FILE: str = "C:/PYTHON/.data/Google_recherche_resultat.txt"

GOOGLE_API_KEY: str = "AIzaSyANXjkhXF4Gkh9k0fY6FWXTtm8xaVi-YoI"
GOOGLE_CX: str = "e6979c65f1e394cf9"

RESULTS_PER_KEYWORD: int = 100  # Google Custom Search retourne 10 par page -> 10 appels
REQUEST_TIMEOUT_SEC: int = 20
SLEEP_BETWEEN_REQUESTS_SEC: float = 1.0  # évite de bombarder


# ----------------------------
# GOOGLE SEARCH (API OFFICIELLE)
# ----------------------------
def google_custom_search(
    query: str,
    api_key: str,
    cx: str,
    max_results: int = 100,
) -> List[str]:
    """
    Retourne une liste d'URLs pour la requête.
    Limite API : 10 résultats par requête, start=1..91 (par pas de 10) pour atteindre 100.
    """
    urls: List[str] = []
    start = 1

    while len(urls) < max_results:
        params = {
            "key": api_key,
            "cx": cx,
            "q": query,
            "num": 10,
            "start": start,
        }

        r = requests.get(
            "https://www.googleapis.com/customsearch/v1",
            params=params,
            timeout=REQUEST_TIMEOUT_SEC,
        )

        if r.status_code != 200:
            # Erreur API : on s'arrête
            raise RuntimeError(
                f"Erreur API Google (status {r.status_code}) pour '{query}': {r.text}"
            )

        data = r.json()
        items = data.get("items", [])
        if not items:
            break

        for it in items:
            link = it.get("link")
            if link:
                urls.append(link)
                if len(urls) >= max_results:
                    break

        start += 10
        if start > 91:  # sécurité pour ne pas dépasser 100
            break

        time.sleep(SLEEP_BETWEEN_REQUESTS_SEC)

    # dédoublonne en gardant l'ordre
    seen = set()
    unique_urls = []
    for u in urls:
        if u not in seen:
            seen.add(u)
            unique_urls.append(u)

    return unique_urls[:max_results]


# ----------------------------
# EXTRACTION DE CONTENU
# ----------------------------
def clean_text(text: str) -> str:
    # Nettoyage simple : espaces multiples, lignes vides
    text = re.sub(r"\r\n?", "\n", text)
    text = re.sub(r"[ \t]+\n", "\n", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    text = re.sub(r"[ \t]{2,}", " ", text)
    return text.strip()


def extract_page_text(url: str) -> Tuple[bool, str]:
    """
    Retourne (ok, contenu).
    ok=False si inaccessible / erreur.
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

        # Certaines pages renvoient du binaire/vidéo/etc. On garde seulement HTML/text
        ctype = resp.headers.get("Content-Type", "")
        if "text/html" not in ctype and "application/xhtml+xml" not in ctype:
            return False, f"[INACCESSIBLE] Content-Type non HTML: {ctype}"

        soup = BeautifulSoup(resp.text, "html.parser")

        # Retirer ce qui pollue
        for tag in soup(["script", "style", "noscript", "header", "footer", "nav", "aside"]):
            tag.decompose()

        text = soup.get_text(separator="\n")
        text = clean_text(text)

        if not text:
            return False, "[INACCESSIBLE] Contenu vide ou non extractible"

        return True, text

    except requests.exceptions.RequestException as e:
        return False, f"[INACCESSIBLE] Erreur réseau: {e}"


# ----------------------------
# ÉCRITURE FICHIER SORTIE
# ----------------------------
def write_results(output_file: str, results: List[Tuple[str, bool, str]]) -> None:
    """
    Format demandé :
    lien
    contenu
    lien
    contenu
    etc.
    """
    with open(output_file, "w", encoding="utf-8") as f:
        for url, ok, content in results:
            f.write(url.strip() + "\n")
            f.write(content.strip() + "\n\n")  # ligne vide entre blocs


# ----------------------------
# MAIN
# ----------------------------
def main() -> None:
    print("{} - Début du programme".format(datetime.now().strftime("%d/%m/%Y %H:%M:%S")))

    all_results: List[Tuple[str, bool, str]] = []
    already_done = set()

    for kw in KEYWORDS:
        print("{} - Recherche du mot clé : {}".format(datetime.now().strftime("%d/%m/%Y %H:%M:%S"), kw))
        urls = google_custom_search(
            query=kw,
            api_key=GOOGLE_API_KEY,
            cx=GOOGLE_CX,
            max_results=RESULTS_PER_KEYWORD,
        )
        print("{} - liens trouvés (avant dédoublonnage global) : {}".format(datetime.now().strftime("%d/%m/%Y %H:%M:%S"), {len(urls)}))
        for i, url in enumerate(urls, start=1):
            if url in already_done:
                continue
            already_done.add(url)

            print("{} - Ouveture URL : {}".format(datetime.now().strftime("%d/%m/%Y %H:%M:%S"), {url}))
            
            ok, content = extract_page_text(url)
            all_results.append((url, ok, content))

            time.sleep(SLEEP_BETWEEN_REQUESTS_SEC)

    print("{} - Ecriture du fichier de sortie".format(datetime.now().strftime("%d/%m/%Y %H:%M:%S")))
    write_results(OUTPUT_FILE, all_results)
    print("{} - Nombre total de pages traitées : ".format(datetime.now().strftime("%d/%m/%Y %H:%M:%S"), {len(all_results)}))
    print("{} - Fin du programme".format(datetime.now().strftime("%d/%m/%Y %H:%M:%S")))

if __name__ == "__main__":
    main()
