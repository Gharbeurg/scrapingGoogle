import os
import re
import time
import mimetypes
from pathlib import Path
from datetime import datetime
from urllib.parse import urlparse, urlunparse, parse_qsl, urlencode, quote_plus
from urllib.parse import urlparse, urlunparse, parse_qsl, urlencode, quote_plus, unquote
from urllib.parse import parse_qs

import requests

from selenium import webdriver
from selenium.webdriver.common.by import By
from selenium.webdriver.common.keys import Keys
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.chrome.service import Service
from selenium.common.exceptions import (
    TimeoutException,
    WebDriverException,
    NoSuchElementException,
    ElementClickInterceptedException,
    StaleElementReferenceException,
)

# webdriver-manager simplifie la gestion du driver Chrome
# pip install webdriver-manager
from webdriver_manager.chrome import ChromeDriverManager


# =========================================================
# CONFIGURATION
# =========================================================

SEARCH_TERMS = [
    "bpco"
]

# Nombre maximum de résultats à récupérer par requête
MAX_RESULTS_PER_QUERY = 10

# Ordre de priorité des moteurs
SEARCH_ENGINES_PRIORITY = ["duckduckgo"]

# Dossiers / fichiers
OUTPUT_DIR = Path(r"C:/PYTHON/.data/GOOGLE_SCRAP")
LOG_FILE = Path(r"C:/PYTHON/.data/GOOGLE_SCRAP/download_log.txt")
SEEN_URLS_FILE = Path(r"C:/PYTHON/.data/GOOGLE_SCRAP/seen_urls.txt")

# Navigateur
HEADLESS = False  # False conseillé pour cookies / captcha / intervention manuelle
WINDOW_WIDTH = 1400
WINDOW_HEIGHT = 1000
PAGE_LOAD_TIMEOUT = 40
ACTION_PAUSE_SECONDS = 1.5
SCROLL_PAUSE_SECONDS = 1.0

# Réseau
REQUEST_TIMEOUT = 60
USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/130.0.0.0 Safari/537.36"
)

# Téléchargement
DEFAULT_BINARY_EXTENSION = ".bin"

# Sauvegarde mémoire
TRACK_FINAL_URL_TOO = True

# Si True, le programme essaie de cliquer sur les boutons cookies connus
AUTO_HANDLE_COOKIES = True

# Limite de temps avant chargement d'une page de résultat suivante
RESULTS_PAGE_WAIT_SECONDS = 12


# =========================================================
# OUTILS GÉNÉRAUX
# =========================================================
def is_search_engine_redirect(url: str) -> bool:
    if not url:
        return False

    try:
        parsed = urlparse(url)
        domain = (parsed.netloc or "").lower()
        path = (parsed.path or "").lower()

        if "duckduckgo.com" in domain and path.startswith("/l/"):
            return True

        if "bing.com" in domain and path.startswith("/ck/"):
            return True

        return False
    except Exception:
        return False
    
def is_search_engine_redirect(url: str) -> bool:
    if not url:
        return False

    try:
        parsed = urlparse(url)
        domain = (parsed.netloc or "").lower()
        path = (parsed.path or "").lower()

        if "duckduckgo.com" in domain and path.startswith("/l/"):
            return True

        return False
    except Exception:
        return False
    
def decode_duckduckgo_redirect(url: str) -> str:
    if not url:
        return url

    try:
        parsed = urlparse(url)
        domain = (parsed.netloc or "").lower()

        if "duckduckgo.com" in domain and parsed.path.startswith("/l/"):
            params = parse_qs(parsed.query)
            uddg_values = params.get("uddg", [])
            if uddg_values:
                return unquote(uddg_values[0])

        return url
    except Exception:
        return url
    
def ensure_output_dir(output_dir: Path) -> None:
    output_dir.mkdir(parents=True, exist_ok=True)


def now_string() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def normalize_url(url: str) -> str:
    if not url:
        return ""

    url = url.strip()

    try:
        url = decode_duckduckgo_redirect(url)

        parsed = urlparse(url)

        scheme = parsed.scheme.lower() if parsed.scheme else "https"
        netloc = parsed.netloc.lower()

        if netloc.startswith("www."):
            netloc = netloc[4:]

        path = parsed.path or "/"
        if path != "/" and path.endswith("/"):
            path = path[:-1]

        fragment = ""

        tracking_prefixes = (
            "utm_",
            "fbclid",
            "gclid",
            "msclkid",
            "mc_cid",
            "mc_eid",
        )

        cleaned_query_items = []
        for key, value in parse_qsl(parsed.query, keep_blank_values=True):
            if any(key.lower().startswith(prefix) for prefix in tracking_prefixes):
                continue
            cleaned_query_items.append((key, value))

        query = urlencode(cleaned_query_items, doseq=True)

        rebuilt = urlunparse((scheme, netloc, path, "", query, fragment))
        return rebuilt
    except Exception:
        return url
    


def guess_extension(content_type: str, url: str) -> str:
    content_type = (content_type or "").split(";")[0].strip().lower()

    extension_map = {
        "text/html": ".html",
        "application/pdf": ".pdf",
        "text/plain": ".txt",
        "application/json": ".json",
        "application/xml": ".xml",
        "text/xml": ".xml",
        "image/jpeg": ".jpg",
        "image/png": ".png",
        "image/gif": ".gif",
        "image/webp": ".webp",
        "application/zip": ".zip",
        "application/msword": ".doc",
        "application/vnd.openxmlformats-officedocument.wordprocessingml.document": ".docx",
        "application/vnd.ms-excel": ".xls",
        "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet": ".xlsx",
        "application/vnd.ms-powerpoint": ".ppt",
        "application/vnd.openxmlformats-officedocument.presentationml.presentation": ".pptx",
    }

    if content_type in extension_map:
        return extension_map[content_type]

    guessed = mimetypes.guess_extension(content_type)
    if guessed:
        return guessed

    parsed = urlparse(url)
    suffix = Path(parsed.path).suffix
    if suffix and len(suffix) <= 8:
        return suffix.lower()

    return DEFAULT_BINARY_EXTENSION


def build_output_filename(index: int, extension: str) -> str:
    extension = extension if extension.startswith(".") else f".{extension}"
    return f"page_{index}{extension}"


def safe_text(text: str) -> str:
    return (text or "").strip()


# =========================================================
# URLS DÉJÀ VUES
# =========================================================

def load_seen_urls(seen_file: Path) -> set:
    if not seen_file.exists():
        return set()

    seen = set()
    with seen_file.open("r", encoding="utf-8") as f:
        for line in f:
            value = line.strip()
            if value:
                seen.add(value)
    return seen


def append_seen_url(seen_file: Path, url: str) -> None:
    with seen_file.open("a", encoding="utf-8") as f:
        f.write(url + "\n")


def is_already_seen(url: str, seen_urls: set) -> bool:
    return normalize_url(url) in seen_urls


# =========================================================
# LOG
# =========================================================

def init_log_file(log_file: Path) -> None:
    if not log_file.exists():
        with log_file.open("w", encoding="utf-8") as f:
            f.write("JOURNAL DES TELECHARGEMENTS\n")
            f.write("=" * 60 + "\n\n")


def format_log_entry(entry: dict) -> str:
    parts = [
        f"DATE : {entry.get('timestamp', '')}",
        f"REQUETE : {entry.get('query', '')}",
        f"RANG : {entry.get('rank', '')}",
        f"MOTEUR : {entry.get('engine', '')}",
        f"URL : {entry.get('url', '')}",
        f"URL FINALE : {entry.get('final_url', '')}",
        f"STATUT : {entry.get('status', '')}",
        f"TYPE : {entry.get('content_type', '')}",
        f"FICHIER : {entry.get('file_path', '')}",
        f"MESSAGE : {entry.get('message', '')}",
        "-" * 60,
    ]
    return "\n".join(parts) + "\n"


def log_result(log_file: Path, entry: dict) -> None:
    with log_file.open("a", encoding="utf-8") as f:
        f.write(format_log_entry(entry))


# =========================================================
# NAVIGATEUR
# =========================================================

def create_browser(headless: bool = False) -> webdriver.Chrome:
    options = Options()
    options.add_argument(f"--user-agent={USER_AGENT}")
    options.add_argument(f"--window-size={WINDOW_WIDTH},{WINDOW_HEIGHT}")
    options.add_argument("--disable-blink-features=AutomationControlled")
    options.add_argument("--disable-notifications")
    options.add_argument("--start-maximized")
    options.add_argument("--lang=fr-FR")
    options.add_experimental_option("excludeSwitches", ["enable-automation"])
    options.add_experimental_option("useAutomationExtension", False)

    if headless:
        options.add_argument("--headless=new")

    service = Service(ChromeDriverManager().install())
    driver = webdriver.Chrome(service=service, options=options)
    driver.set_page_load_timeout(PAGE_LOAD_TIMEOUT)

    try:
        driver.execute_script(
            """
            Object.defineProperty(navigator, 'webdriver', {
                get: () => undefined
            })
            """
        )
    except Exception:
        pass

    return driver


def wait_for_page_ready(driver: webdriver.Chrome, timeout: int = PAGE_LOAD_TIMEOUT) -> None:
    start = time.time()
    while time.time() - start < timeout:
        try:
            state = driver.execute_script("return document.readyState")
            if state == "complete":
                return
        except Exception:
            pass
        time.sleep(0.5)


# =========================================================
# COOKIES / CAPTCHA / BLOCAGES
# =========================================================

def click_button_by_text(driver: webdriver.Chrome, texts: list) -> bool:
    xpaths = []

    for txt in texts:
        txt_norm = txt.strip()
        xpaths.extend([
            f"//button[contains(translate(normalize-space(.), "
            f"'ABCDEFGHIJKLMNOPQRSTUVWXYZÀÂÄÇÉÈÊËÎÏÔÖÙÛÜŸ', "
            f"'abcdefghijklmnopqrstuvwxyzàâäçéèêëîïôöùûüÿ'), "
            f"'{txt_norm.lower()}')]",
            f"//a[contains(translate(normalize-space(.), "
            f"'ABCDEFGHIJKLMNOPQRSTUVWXYZÀÂÄÇÉÈÊËÎÏÔÖÙÛÜŸ', "
            f"'abcdefghijklmnopqrstuvwxyzàâäçéèêëîïôöùûüÿ'), "
            f"'{txt_norm.lower()}')]",
            f"//*[@role='button' and contains(translate(normalize-space(.), "
            f"'ABCDEFGHIJKLMNOPQRSTUVWXYZÀÂÄÇÉÈÊËÎÏÔÖÙÛÜŸ', "
            f"'abcdefghijklmnopqrstuvwxyzàâäçéèêëîïôöùûüÿ'), "
            f"'{txt_norm.lower()}')]",
            f"//input[@type='submit' and contains(translate(@value, "
            f"'ABCDEFGHIJKLMNOPQRSTUVWXYZÀÂÄÇÉÈÊËÎÏÔÖÙÛÜŸ', "
            f"'abcdefghijklmnopqrstuvwxyzàâäçéèêëîïôöùûüÿ'), "
            f"'{txt_norm.lower()}')]",
        ])

    for xpath in xpaths:
        try:
            elements = driver.find_elements(By.XPATH, xpath)
            for el in elements:
                try:
                    if el.is_displayed():
                        driver.execute_script("arguments[0].scrollIntoView({block: 'center'});", el)
                        time.sleep(0.2)
                        driver.execute_script("arguments[0].click();", el)
                        time.sleep(ACTION_PAUSE_SECONDS)
                        return True
                except (ElementClickInterceptedException, StaleElementReferenceException, WebDriverException):
                    continue
        except Exception:
            continue

    return False


def handle_cookie_banners(driver: webdriver.Chrome) -> bool:
    if not AUTO_HANDLE_COOKIES:
        return False

    texts = [
        "accepter",
        "tout accepter",
        "j'accepte",
        "autoriser",
        "allow all",
        "accept",
        "accept all",
        "i agree",
        "agree",
        "ok",
    ]

    # Essai page principale
    if click_button_by_text(driver, texts):
        return True

    # Essai dans les iframes
    try:
        frames = driver.find_elements(By.TAG_NAME, "iframe")
        for frame in frames:
            try:
                driver.switch_to.frame(frame)
                if click_button_by_text(driver, texts):
                    driver.switch_to.default_content()
                    return True
                driver.switch_to.default_content()
            except Exception:
                driver.switch_to.default_content()
                continue
    except Exception:
        driver.switch_to.default_content()

    return False


def detect_captcha(driver: webdriver.Chrome) -> bool:
    try:
        page_text = driver.page_source.lower()
    except Exception:
        return False

    markers = [
        "captcha",
        "recaptcha",
        "g-recaptcha",
        "hcaptcha",
        "i am not a robot",
        "i'm not a robot",
        "je ne suis pas un robot",
        "/sorry/index",
        "unusual traffic",
    ]

    if any(marker in page_text for marker in markers):
        return True

    try:
        iframes = driver.find_elements(By.TAG_NAME, "iframe")
        for iframe in iframes:
            src = (iframe.get_attribute("src") or "").lower()
            title = (iframe.get_attribute("title") or "").lower()
            if "captcha" in src or "recaptcha" in src or "captcha" in title or "recaptcha" in title:
                return True
    except Exception:
        pass

    return False


def detect_login_wall(driver: webdriver.Chrome) -> bool:
    try:
        page_text = driver.page_source.lower()
    except Exception:
        return False

    markers = [
        "sign in to continue",
        "log in to continue",
        "connectez-vous",
        "se connecter",
        "connexion obligatoire",
        "subscribe to continue",
        "abonnez-vous pour continuer",
    ]
    return any(marker in page_text for marker in markers)


def wait_for_manual_resolution(driver: webdriver.Chrome, reason: str) -> bool:
    print("\n" + "=" * 80)
    print(f"BLOCAGE DETECTE : {reason}")
    print("Le navigateur reste ouvert.")
    print("Résous le problème manuellement dans le navigateur, puis appuie sur Entrée ici.")
    input("Appuie sur Entrée pour reprendre...")
    print("=" * 80 + "\n")

    time.sleep(2)

    # Vérifie si le blocage semble disparu
    if reason.lower().startswith("captcha") and not detect_captcha(driver):
        return True

    if reason.lower().startswith("connexion") and not detect_login_wall(driver):
        return True

    return True


# =========================================================
# RECHERCHE
# =========================================================

def build_search_url(query: str, engine: str) -> str:
    encoded = quote_plus(query)

    if engine == "duckduckgo":
        return f"https://duckduckgo.com/html/?q={encoded}"

    raise ValueError(f"Moteur non supporté : {engine}")


def is_sponsored_result(text: str, element) -> bool:
    text_low = (text or "").lower()

    sponsored_markers = [
        "sponsored",
        "annonce",
        "ad",
        "ads",
        "publicité",
    ]

    if any(marker in text_low for marker in sponsored_markers):
        return True

    try:
        class_name = (element.get_attribute("class") or "").lower()
        if "ad" in class_name or "sponsored" in class_name:
            return True
    except Exception:
        pass

    return False


def extract_duckduckgo_results(driver: webdriver.Chrome) -> list:
    results = []

    selectors = [
        "a.result__a",
        "h2 a",
        "a[data-testid='result-title-a']",
    ]

    links = []
    for selector in selectors:
        try:
            links = driver.find_elements(By.CSS_SELECTOR, selector)
            if links:
                break
        except Exception:
            continue

    rank = 1
    for link in links:
        try:
            title = safe_text(link.text)
            url = safe_text(link.get_attribute("href"))

            if not url:
                continue

            url = decode_duckduckgo_redirect(url)
            url = normalize_url(url)

            if not url:
                continue

            # Ignore les liens restant chez DuckDuckGo
            if "duckduckgo.com" in urlparse(url).netloc.lower():
                continue

            results.append({
                "rank": rank,
                "title": title or url,
                "url": url,
            })
            rank += 1

        except Exception:
            continue

    return results


def extract_organic_results(driver: webdriver.Chrome, engine: str) -> list:
    return extract_duckduckgo_results(driver)


def click_next_results_page(driver: webdriver.Chrome, engine: str) -> bool:
    selectors = [
        (By.CSS_SELECTOR, "a.result--more__btn"),
        (By.XPATH, "//a[contains(., 'Next')]"),
        (By.XPATH, "//input[@type='submit' and contains(@value, 'Next')]"),
        (By.XPATH, "//button[contains(., 'Next')]"),
    ]

    for by, selector in selectors:
        try:
            elements = driver.find_elements(by, selector)
            for el in elements:
                try:
                    if el.is_displayed():
                        driver.execute_script("arguments[0].scrollIntoView({block: 'center'});", el)
                        time.sleep(0.5)
                        driver.execute_script("arguments[0].click();", el)
                        time.sleep(SCROLL_PAUSE_SECONDS)
                        wait_for_page_ready(driver, RESULTS_PAGE_WAIT_SECONDS)
                        return True
                except Exception:
                    continue
        except Exception:
            continue

    return False


def dedupe_results_keep_order(results: list) -> list:
    seen = set()
    clean = []

    for item in results:
        norm = normalize_url(item.get("url", ""))
        if not norm:
            continue
        if norm in seen:
            continue
        seen.add(norm)
        item["url"] = norm
        clean.append(item)

    # remet les rangs à plat
    for i, item in enumerate(clean, start=1):
        item["rank"] = i

    return clean


def search_query_with_engine(driver: webdriver.Chrome, query: str, max_results: int, engine: str) -> list:
    url = build_search_url(query, engine)
    driver.get(url)
    wait_for_page_ready(driver)

    handle_cookie_banners(driver)

    all_results = []
    pages_guard = 0

    while len(all_results) < max_results and pages_guard < 15:
        pages_guard += 1

        if detect_captcha(driver):
            wait_for_manual_resolution(driver, "Captcha sur la page de recherche")
            wait_for_page_ready(driver)

        page_results = extract_organic_results(driver, engine)
        all_results.extend(page_results)
        all_results = dedupe_results_keep_order(all_results)

        if len(all_results) >= max_results:
            break

        moved = click_next_results_page(driver, engine)
        if not moved:
            break

        handle_cookie_banners(driver)

    all_results = all_results[:max_results]

    for i, item in enumerate(all_results, start=1):
        item["query"] = query
        item["rank"] = i
        item["engine"] = engine

    return all_results


def search_query(driver: webdriver.Chrome, query: str, max_results: int, engines_priority: list) -> list:
    last_error = None

    for engine in engines_priority:
        try:
            print(f"\nRecherche avec {engine} pour : {query}")
            results = search_query_with_engine(driver, query, max_results, engine)
            if results:
                return results
        except Exception as e:
            last_error = e
            print(f"Échec moteur {engine} : {e}")

    if last_error:
        raise last_error

    return []


def filter_results(results: list, seen_urls: set) -> list:
    filtered = []
    internal_seen = set()

    blocked_domains = {
        "duckduckgo.com",
    }

    for item in results:
        url = normalize_url(item.get("url", ""))
        if not url:
            continue

        if is_search_engine_redirect(url):
            continue

        try:
            domain = urlparse(url).netloc.lower()
        except Exception:
            domain = ""

        if domain in blocked_domains:
            continue

        if url in internal_seen:
            continue

        if url in seen_urls:
            continue

        internal_seen.add(url)
        item["url"] = url
        filtered.append(item)

    return filtered


# =========================================================
# OUVERTURE / CONTENU
# =========================================================

def open_url(driver: webdriver.Chrome, url: str, timeout: int = PAGE_LOAD_TIMEOUT) -> dict:
    try:
        driver.set_page_load_timeout(timeout)
        driver.get(url)
        wait_for_page_ready(driver, timeout=timeout)
        time.sleep(2)

        final_url = safe_text(driver.current_url)

        for _ in range(5):
            if not is_search_engine_redirect(final_url) and "duckduckgo.com" not in final_url.lower():
                break
            time.sleep(1)
            final_url = safe_text(driver.current_url)

        if detect_captcha(driver):
            return {
                "success": False,
                "final_url": final_url,
                "issue": "captcha",
                "message": "Captcha détecté",
            }

        if detect_login_wall(driver):
            return {
                "success": False,
                "final_url": final_url,
                "issue": "login_wall",
                "message": "Connexion probablement requise",
            }

        if is_search_engine_redirect(final_url) or "bing.com" in final_url.lower() or "duckduckgo.com" in final_url.lower():
            return {
                "success": False,
                "final_url": final_url,
                "issue": "redirect_not_resolved",
                "message": "Resté sur une URL intermédiaire du moteur de recherche",
            }

        return {
            "success": True,
            "final_url": final_url,
            "issue": None,
            "message": "OK",
        }

    except TimeoutException:
        return {
            "success": False,
            "final_url": safe_text(getattr(driver, "current_url", url)),
            "issue": "timeout",
            "message": "Temps de chargement dépassé",
        }
    except Exception as e:
        return {
            "success": False,
            "final_url": safe_text(getattr(driver, "current_url", url)),
            "issue": "open_error",
            "message": f"Erreur ouverture : {e}",
        }


def get_cookies_for_requests(driver: webdriver.Chrome) -> dict:
    cookies = {}
    try:
        for cookie in driver.get_cookies():
            name = cookie.get("name")
            value = cookie.get("value")
            if name:
                cookies[name] = value
    except Exception:
        pass
    return cookies


def build_request_headers() -> dict:
    return {
        "User-Agent": USER_AGENT,
        "Accept": "*/*",
        "Accept-Language": "fr-FR,fr;q=0.9,en;q=0.8",
        "Connection": "keep-alive",
    }


def probe_content_type(url: str, cookies: dict | None = None, headers: dict | None = None) -> tuple[str, str]:
    cookies = cookies or {}
    headers = headers or build_request_headers()

    try:
        with requests.Session() as session:
            response = session.get(
                url,
                headers=headers,
                cookies=cookies,
                timeout=REQUEST_TIMEOUT,
                allow_redirects=True,
                stream=True,
            )
            final_url = response.url
            content_type = response.headers.get("Content-Type", "").strip()
            response.close()
            return content_type, final_url
    except Exception:
        return "", url


def detect_content_type(driver: webdriver.Chrome, url: str) -> tuple[str, str]:
    cookies = get_cookies_for_requests(driver)
    headers = build_request_headers()

    content_type, final_url = probe_content_type(url, cookies=cookies, headers=headers)

    if content_type:
        return content_type, final_url

    # secours : essaie de déduire depuis le navigateur
    try:
        current_url = safe_text(driver.current_url)
        page_source = driver.page_source.lower()
        if "<html" in page_source:
            return "text/html", current_url or url
    except Exception:
        pass

    return "", url


def save_html_page(driver: webdriver.Chrome, filepath: Path) -> None:
    html = driver.page_source
    filepath.write_text(html, encoding="utf-8", errors="ignore")


def download_binary(url: str, filepath: Path, cookies: dict | None = None, headers: dict | None = None) -> dict:
    cookies = cookies or {}
    headers = headers or build_request_headers()

    try:
        with requests.Session() as session:
            with session.get(
                url,
                headers=headers,
                cookies=cookies,
                timeout=REQUEST_TIMEOUT,
                allow_redirects=True,
                stream=True,
            ) as response:
                response.raise_for_status()
                content_type = response.headers.get("Content-Type", "").strip()
                final_url = response.url

                with filepath.open("wb") as f:
                    for chunk in response.iter_content(chunk_size=8192):
                        if chunk:
                            f.write(chunk)

        return {
            "success": True,
            "content_type": content_type,
            "final_url": final_url,
            "message": "Téléchargement binaire OK",
        }

    except Exception as e:
        return {
            "success": False,
            "content_type": "",
            "final_url": url,
            "message": f"Échec téléchargement binaire : {e}",
        }


def save_content(driver: webdriver.Chrome, url: str, output_dir: Path, file_index: int) -> dict:
    cookies = get_cookies_for_requests(driver)
    headers = build_request_headers()

    content_type, probed_final_url = detect_content_type(driver, url)
    content_type_low = (content_type or "").lower()

    # HTML
    if "text/html" in content_type_low or not content_type_low:
        extension = ".html"
        filename = build_output_filename(file_index, extension)
        filepath = output_dir / filename

        save_html_page(driver, filepath)

        return {
            "success": True,
            "content_type": content_type or "text/html",
            "final_url": probed_final_url or url,
            "file_path": str(filepath),
            "message": "Page HTML sauvegardée",
        }

    # Non-HTML
    extension = guess_extension(content_type, probed_final_url or url)
    filename = build_output_filename(file_index, extension)
    filepath = output_dir / filename

    download_result = download_binary(
        url=probed_final_url or url,
        filepath=filepath,
        cookies=cookies,
        headers=headers,
    )

    if download_result["success"]:
        return {
            "success": True,
            "content_type": download_result.get("content_type", content_type),
            "final_url": download_result.get("final_url", probed_final_url or url),
            "file_path": str(filepath),
            "message": download_result.get("message", "Téléchargement OK"),
        }

    return {
        "success": False,
        "content_type": download_result.get("content_type", content_type),
        "final_url": download_result.get("final_url", probed_final_url or url),
        "file_path": "",
        "message": download_result.get("message", "Échec téléchargement"),
    }


# =========================================================
# PROGRESSION
# =========================================================

def print_progress(current: int, total: int, query: str, url: str, status: str) -> None:
    print("\n" + "=" * 80)
    print(f"[{current}/{total}]")
    print(f"Requête : {query}")
    print(f"URL     : {url}")
    print(f"Statut  : {status}")
    print("=" * 80)


# =========================================================
# TRAITEMENT CENTRAL
# =========================================================

def process_result(
    driver: webdriver.Chrome,
    result: dict,
    output_dir: Path,
    file_index: int,
    log_file: Path,
    seen_file: Path,
    seen_urls: set,
) -> dict:
    query = result.get("query", "")
    rank = result.get("rank", "")
    engine = result.get("engine", "")
    url = normalize_url(result.get("url", ""))

    entry = {
        "timestamp": now_string(),
        "query": query,
        "rank": rank,
        "engine": engine,
        "url": url,
        "final_url": "",
        "status": "",
        "content_type": "",
        "file_path": "",
        "message": "",
    }

    try:
        if is_already_seen(url, seen_urls):
            entry["status"] = "SKIPPED_ALREADY_SEEN"
            entry["message"] = "URL déjà traitée"
            log_result(log_file, entry)
            return entry

        open_result = open_url(driver, url)
        entry["final_url"] = open_result.get("final_url", "")

        # tentative cookies après ouverture
        cookies_clicked = handle_cookie_banners(driver)
        if cookies_clicked:
            time.sleep(1)

            # après clic cookies, nouvelle vérification éventuelle
            if detect_captcha(driver):
                open_result = {
                    "success": False,
                    "final_url": safe_text(driver.current_url),
                    "issue": "captcha",
                    "message": "Captcha détecté après gestion cookies",
                }

        if not open_result["success"]:
            issue = open_result.get("issue", "")

            if issue == "captcha":
                resolved = wait_for_manual_resolution(driver, "Captcha")
                if resolved:
                    time.sleep(2)
                    wait_for_page_ready(driver)
                else:
                    entry["status"] = "CAPTCHA_UNRESOLVED"
                    entry["message"] = open_result["message"]
                    log_result(log_file, entry)
                    return entry

            elif issue == "login_wall":
                resolved = wait_for_manual_resolution(driver, "Connexion ou mur d'accès")
                if resolved:
                    time.sleep(2)
                    wait_for_page_ready(driver)
                else:
                    entry["status"] = "LOGIN_WALL"
                    entry["message"] = open_result["message"]
                    log_result(log_file, entry)
                    return entry

            else:
                entry["status"] = "OPEN_ERROR"
                entry["message"] = open_result["message"]
                log_result(log_file, entry)
                return entry

        # Vérifie qu'on n'est pas resté sur un moteur de recherche
        final_url_check = safe_text(driver.current_url)
        final_domain = urlparse(final_url_check).netloc.lower()

        if "duckduckgo.com" in final_domain:
            entry["status"] = "SEARCH_ENGINE_PAGE_SKIPPED"
            entry["message"] = "Page du moteur de recherche ignorée"
            entry["final_url"] = final_url_check
            log_result(log_file, entry)
            return entry
        
        # Sauvegarde
        save_result = save_content(driver, safe_text(driver.current_url) or url, output_dir, file_index)
        entry["final_url"] = save_result.get("final_url", safe_text(driver.current_url) or url)
        entry["content_type"] = save_result.get("content_type", "")
        entry["file_path"] = save_result.get("file_path", "")
        entry["message"] = save_result.get("message", "")

        if save_result["success"]:
            entry["status"] = "SUCCESS"

            norm_original = normalize_url(url)
            if norm_original and norm_original not in seen_urls:
                seen_urls.add(norm_original)
                append_seen_url(seen_file, norm_original)

            if TRACK_FINAL_URL_TOO:
                norm_final = normalize_url(entry["final_url"])
                if norm_final and norm_final not in seen_urls:
                    seen_urls.add(norm_final)
                    append_seen_url(seen_file, norm_final)
        else:
            entry["status"] = "DOWNLOAD_FAILED"

        log_result(log_file, entry)
        return entry

    except Exception as e:
        entry["status"] = "UNKNOWN_ERROR"
        entry["message"] = str(e)
        log_result(log_file, entry)
        return entry


def process_query(
    driver: webdriver.Chrome,
    query: str,
    max_results: int,
    output_dir: Path,
    log_file: Path,
    seen_file: Path,
    seen_urls: set,
    start_index: int,
) -> int:
    try:
        raw_results = search_query(
            driver=driver,
            query=query,
            max_results=max_results,
            engines_priority=SEARCH_ENGINES_PRIORITY,
        )
    except Exception as e:
        entry = {
            "timestamp": now_string(),
            "query": query,
            "rank": "",
            "engine": "",
            "url": "",
            "final_url": "",
            "status": "SEARCH_ERROR",
            "content_type": "",
            "file_path": "",
            "message": str(e),
        }
        log_result(log_file, entry)
        print(f"Erreur recherche pour '{query}' : {e}")
        return start_index

    results = filter_results(raw_results, seen_urls)

    if not results:
        print(f"Aucun nouveau résultat pour : {query}")
        return start_index

    file_index = start_index
    total = len(results)

    for current, result in enumerate(results, start=1):
        print_progress(
            current=current,
            total=total,
            query=query,
            url=result.get("url", ""),
            status="TRAITEMENT EN COURS",
        )

        processed = process_result(
            driver=driver,
            result=result,
            output_dir=output_dir,
            file_index=file_index,
            log_file=log_file,
            seen_file=seen_file,
            seen_urls=seen_urls,
        )

        print_progress(
            current=current,
            total=total,
            query=query,
            url=result.get("url", ""),
            status=f"{processed.get('status', '')} -> {processed.get('file_path', '')}",
        )

        if processed.get("status") == "SUCCESS":
            file_index += 1

    return file_index


# =========================================================
# MAIN
# =========================================================

def main() -> None:
    ensure_output_dir(OUTPUT_DIR)
    init_log_file(LOG_FILE)
    seen_urls = load_seen_urls(SEEN_URLS_FILE)

    print("Démarrage du programme")
    print(f"Dossier de sortie : {OUTPUT_DIR.resolve()}")
    print(f"Fichier log       : {LOG_FILE.resolve()}")
    print(f"URLs déjà vues    : {len(seen_urls)}")
    print("-" * 60)

    driver = create_browser(headless=HEADLESS)
    next_file_index = 1

    # si des fichiers page_X existent déjà, reprendre après le dernier index
    existing_numbers = []
    for file in OUTPUT_DIR.glob("page_*.*"):
        match = re.match(r"page_(\d+)\..+$", file.name)
        if match:
            existing_numbers.append(int(match.group(1)))
    if existing_numbers:
        next_file_index = max(existing_numbers) + 1

    try:
        for query in SEARCH_TERMS:
            next_file_index = process_query(
                driver=driver,
                query=query,
                max_results=MAX_RESULTS_PER_QUERY,
                output_dir=OUTPUT_DIR,
                log_file=LOG_FILE,
                seen_file=SEEN_URLS_FILE,
                seen_urls=seen_urls,
                start_index=next_file_index,
            )
    finally:
        driver.quit()

    print("\nTraitement terminé.")


if __name__ == "__main__":
    main()