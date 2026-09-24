"""Bounded HTTP transport and parsers for the public Qi Scans pages."""
from collections import OrderedDict
from copy import deepcopy
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
import json
import re
from threading import RLock
from time import monotonic
from urllib.parse import urlencode, urljoin, urlsplit

from bs4 import BeautifulSoup
import requests

BASE_URL = "https://qiscansmanga.org"
IMAGE_HOSTS = {"qiscansmanga.org", "i.imgur.com", *(f"i{i}.wp.com" for i in range(4))}
SLUG = re.compile(r"[a-z0-9]+(?:-[a-z0-9]+)*\Z")


class SourceError(Exception):
    def __init__(self, message, status=502, retry_after=None):
        super().__init__(message)
        self.status = status
        self.retry_after = retry_after


def checked_url(url, image=False):
    """Only exact, known hosts; repeat this check after every redirect."""
    try:
        parsed = urlsplit(url)
        allowed = IMAGE_HOSTS if image else {urlsplit(BASE_URL).hostname}
        valid = (parsed.scheme == "https" and parsed.hostname in allowed
                 and parsed.port in (None, 443) and not parsed.username
                 and not parsed.password and not parsed.fragment)
    except (ValueError, TypeError):
        valid = False
    if not valid:
        raise SourceError("URL fora dos endereços permitidos.", 400)
    return url


def slug(value):
    if not isinstance(value, str) or not SLUG.fullmatch(value) or len(value) > 240:
        raise SourceError("Identificador inválido.", 400)
    return value


def remote_id(url, kind="manga"):
    parsed = urlsplit(checked_url(urljoin(BASE_URL, url)))
    pattern = r"/manga/([^/]+)/?" if kind == "manga" else r"/([^/]+)/?"
    match = re.fullmatch(pattern, parsed.path)
    if not match or parsed.query:
        raise SourceError("Link de conteúdo inválido.")
    return slug(match[1])


class Transport:
    def __init__(self):
        self.cooldown_until = 0
        self.lock = RLock()

    def get(self, url, *, image=False):
        with self.lock:
            remaining = self.cooldown_until - monotonic()
        if remaining > 0:
            raise SourceError("Fonte temporariamente indisponível.", 503, int(remaining) + 1)
        max_bytes = 20 * 1024 * 1024 if image else 4 * 1024 * 1024
        try:
            for _ in range(5):
                checked_url(url, image)
                # No cookie jar, retries, or execution of upstream JavaScript.
                with requests.get(url, timeout=(3, 20), allow_redirects=False, stream=True,
                                  headers={"User-Agent": "Mangaka-Qiscans/0.1",
                                           "Referer": BASE_URL + "/"}) as response:
                    if response.status_code in (301, 302, 303, 307, 308):
                        location = response.headers.get("Location")
                        if not location:
                            raise SourceError("Redirecionamento inválido.")
                        url = urljoin(url, location)
                        continue
                    if response.status_code == 429:
                        wait = self.retry_seconds(response.headers.get("Retry-After", "60"))
                        self.pause(wait)
                        raise SourceError("Limite de consultas da fonte atingido.", 503, wait)
                    if response.status_code == 404:
                        raise SourceError("Conteúdo não encontrado na fonte.", 404)
                    if response.status_code != 200:
                        self.pause(30)
                        raise SourceError("Fonte temporariamente indisponível.", 503, 30)
                    content_type = response.headers.get("Content-Type", "").split(";")[0].lower()
                    permitted = {"image/jpeg", "image/png", "image/webp", "image/gif", "image/avif"}
                    if (image and content_type not in permitted) or (
                            not image and content_type not in {"text/html", "application/xhtml+xml"}):
                        raise SourceError("Tipo de conteúdo inesperado na fonte.")
                    chunks, size = [], 0
                    for chunk in response.iter_content(65536):
                        size += len(chunk)
                        if size > max_bytes:
                            raise SourceError("Resposta da fonte excedeu o limite de tamanho.")
                        chunks.append(chunk)
                    return b"".join(chunks), content_type
            raise SourceError("Excesso de redirecionamentos da fonte.")
        except requests.RequestException as exc:
            self.pause(30)
            raise SourceError("Não foi possível consultar a fonte.", 503, 30) from exc

    def pause(self, seconds):
        with self.lock:
            self.cooldown_until = max(self.cooldown_until, monotonic() + seconds)

    @staticmethod
    def retry_seconds(value):
        try:
            seconds = int(value)
        except (ValueError, TypeError):
            try:
                date = parsedate_to_datetime(value)
                seconds = int((date - datetime.now(timezone.utc)).total_seconds()) + 1
            except (ValueError, TypeError, OverflowError):
                seconds = 60
        return max(1, seconds)


class Qiscans:
    def __init__(self, transport=None):
        self.transport = transport or Transport()
        self.cache = OrderedDict()
        self.lock = RLock()

    def cached(self, key, loader, ttl=900):
        # A single process shares misses and cooldown across gunicorn threads.
        with self.lock:
            entry = self.cache.get(key)
            if entry and entry[0] > monotonic():
                self.cache.move_to_end(key)
                return deepcopy(entry[1])
            value = loader()
            self.cache[key] = (monotonic() + ttl, deepcopy(value))
            self.cache.move_to_end(key)
            while len(self.cache) > 256:
                self.cache.popitem(last=False)
            return value

    def document(self, path, params=None):
        url = BASE_URL + path
        if params:
            url += "?" + urlencode(params)
        raw, _ = self.transport.get(url)
        return BeautifulSoup(raw, "html.parser")

    @staticmethod
    def text(node):
        return node.get_text(" ", strip=True) if node else ""

    def tags(self):
        def load():
            doc = self.document("/manga/")
            tags = []
            for item in doc.select('input[name="genre[]"]'):
                label = doc.find("label", attrs={"for": item.get("id")})
                identifier = item.get("value", "")
                if identifier.isdigit() and self.text(label):
                    tags.append({"id": identifier, "name": self.text(label)})
            if not tags:
                raise SourceError("A fonte não retornou a lista de gêneros esperada.")
            return tags
        return self.cached("tags", load, 86400)

    def listing(self, page=1, query=None, tag=None):
        key = ("listing", page, query, tag)
        def load():
            if tag and tag not in {t["id"] for t in self.tags()}:
                raise SourceError("Gênero não encontrado.", 404)
            if query:
                path = "/" if page == 1 else f"/page/{page}/"
                params = {"s": query}
            else:
                path, params = "/manga/", {"page": page, "order": "update"}
                if tag:
                    params["genre[]"] = tag
            doc = self.document(path, params)
            container = doc.select_one(".postbody .listupd")
            if container is None:
                raise SourceError("A estrutura do catálogo mudou ou a fonte bloqueou a consulta.")
            cards = container.select(".bsx")
            if not cards and self.text(container).lower() != "not found":
                raise SourceError("A fonte retornou um catálogo vazio sem confirmação.")
            results, seen = [], set()
            for card in cards:
                if card.select_one(".novelabel"):
                    continue
                link = card.select_one('a[href*="/manga/"]')
                title = self.text(card.select_one(".tt"))
                if not link or not title:
                    raise SourceError("A fonte retornou um item de catálogo incompleto.")
                identifier = remote_id(link["href"])
                if identifier in seen:
                    continue
                seen.add(identifier)
                # WordPress search may ignore genre[]; enforce combined filters.
                if query and tag and tag not in {t["id"] for t in self.info(identifier)["tagLinks"]}:
                    continue
                image = card.select_one("img")
                cover = self.image_url(image)
                results.append({"id": identifier, "title": title, "coverUrl": cover})
            next_link = doc.select_one(".postbody .pagination a.next")
            if next_link:
                checked_url(urljoin(BASE_URL, next_link.get("href", "")))
            return {"results": results, "total": None, "has_next": next_link is not None}
        return self.cached(key, load)

    @staticmethod
    def image_url(node):
        if node is None:
            return None
        url = node.get("data-src") or node.get("src")
        return checked_url(urljoin(BASE_URL, url), image=True) if url else None

    def series(self, identifier):
        slug(identifier)
        def load():
            doc = self.document(f"/manga/{identifier}/")
            title = self.text(doc.select_one("h1.entry-title"))
            chapter_list = doc.select_one("#chapterlist")
            if not title or chapter_list is None:
                raise SourceError("A estrutura da obra mudou ou o acesso não está disponível.")
            metadata = {}
            for node in doc.select(".tsinfo .imptdt"):
                value = node.select_one("i, a")
                if value:
                    label = self.text(node).removesuffix(self.text(value)).strip().casefold()
                    metadata[label] = self.text(value)
            if metadata.get("type", "").casefold() == "novel":
                raise SourceError("Esta fonte de imagens não oferece novels.", 422)
            by_name = {t["name"].casefold(): t for t in self.tags()}
            genres = [self.text(a) for a in doc.select(".mgen a")]
            chapters, seen = [], set()
            for item in chapter_list.select("li"):
                # Do not expose entries explicitly marked as locked/premium.
                if item.select_one(".locked, .premium, .fa-lock") or set(item.get("class", [])) & {"locked", "premium"}:
                    continue
                link = item.select_one(".eph-num a")
                if not link:
                    raise SourceError("Capítulo sem link na fonte.")
                chapter_id = remote_id(link["href"], "chapter")
                if chapter_id in seen:
                    continue
                seen.add(chapter_id)
                number = item.get("data-num")
                if not number or not re.fullmatch(r"\d+(?:\.\d+)?", number):
                    number = None
                chapters.append({"id": chapter_id, "number": number,
                                 "title": self.text(item.select_one(".chapternum")),
                                 "lang": "en", "date": self.text(item.select_one(".chapterdate"))})
            year = metadata.get("released", "")
            return {"id": identifier, "title": title,
                    "description": self.text(doc.select_one('.entry-content[itemprop="description"]')),
                    "coverUrl": self.image_url(doc.select_one(".info-left .thumb img")),
                    "genres": genres, "tagLinks": [by_name[g.casefold()] for g in genres if g.casefold() in by_name],
                    "authors": [metadata["author"]] if metadata.get("author") else [],
                    "status": metadata.get("status"), "year": int(year) if year.isdigit() else None,
                    "chapters": chapters}
        return self.cached(("series", identifier), load)

    def info(self, identifier):
        return {k: v for k, v in self.series(identifier).items() if k != "chapters"}

    def chapters(self, identifier):
        records = self.series(identifier)["chapters"]
        return {"chapters": records, "total": len(records)}

    def pages(self, identifier, chapter_id):
        slug(identifier)
        slug(chapter_id)
        def load():
            if chapter_id not in {c["id"] for c in self.series(identifier)["chapters"]}:
                raise SourceError("Capítulo não pertence a esta obra ou está indisponível.", 404)
            doc = self.document(f"/{chapter_id}/")
            parent = doc.select_one('.allc a[href*="/manga/"]')
            if not parent or remote_id(parent["href"]) != identifier:
                raise SourceError("A fonte retornou um capítulo de outra obra.")
            reader = None
            for script in doc.find_all("script", src=False):
                content = script.string or ""
                match = re.search(r"\bts_reader\.run\s*\(\s*", content)
                if match:
                    try:
                        reader, _ = json.JSONDecoder().raw_decode(content[match.end():])
                    except ValueError as exc:
                        raise SourceError("Dados do leitor inválidos.") from exc
                    break
            if not isinstance(reader, dict):
                raise SourceError("A fonte não retornou os dados do leitor.")
            if reader.get("protected") is not False or reader.get("is_novel") is not False:
                raise SourceError("Capítulo protegido ou sem suporte ao leitor de imagens.", 422)
            sources = reader.get("sources")
            if not isinstance(sources, list) or not sources or any(not isinstance(s, dict) for s in sources):
                raise SourceError("Lista de servidores do leitor inválida.")
            selected = next((s for s in sources if s.get("source") == reader.get("defaultSource")), sources[0])
            urls = selected.get("images")
            if not isinstance(urls, list) or not urls or any(not isinstance(u, str) for u in urls):
                raise SourceError("Nenhuma página de imagem disponível.")
            return {"pages": [checked_url(u, image=True) for u in urls]}
        return self.cached(("pages", identifier, chapter_id), load, 600)
