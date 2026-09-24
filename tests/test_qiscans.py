"""Small synthetic fixtures based on the inspected MangaReader markup."""
import json
from urllib.parse import parse_qs, urlsplit

import pytest
import requests

from qiscans.app import create_app
from qiscans.client import BASE_URL, Qiscans, SourceError, Transport, checked_url


TAGS = '''<input name="genre[]" id="genre-3" value="3"><label for="genre-3">Action</label>
<input name="genre[]" id="genre-4" value="4"><label for="genre-4">Sports</label>'''
CARD = '''<div class="bsx"><a href="/manga/story/"><div class="tt">Story &amp; Friends</div>
<img src="https://i0.wp.com/qiscansmanga.org/cover.jpg"></a></div>'''
CATALOG = f'''{TAGS}<div class="postbody"><div class="listupd">{CARD}
<div class="bsx"><span class="novelabel">Novel</span></div></div>
<div class="pagination"><a class="next" href="/manga/?page=2">Next</a></div></div>
<aside><div class="listupd"><div class="bsx">Not a result</div></div></aside>'''
SERIES = '''<h1 class="entry-title">Story &amp; Friends</h1>
<div class="info-left"><div class="thumb"><img src="https://i1.wp.com/qiscansmanga.org/cover.jpg"></div></div>
<div class="tsinfo"><div class="imptdt">Author <i>Example Author</i></div>
<div class="imptdt">Released <i>2024</i></div><div class="imptdt">Status <i>Ongoing</i></div>
<div class="imptdt">Type <a>Manga</a></div></div>
<div class="entry-content" itemprop="description"><p>First paragraph.</p><p>Second paragraph.</p></div>
<div class="mgen"><a href="/genres/action/">Action</a></div>
<ul id="chapterlist">
<li data-num="2.5"><div class="eph-num"><a href="/story-chapter-2-5/"><span class="chapternum">Chapter 2.5</span></a></div></li>
<li data-num="1"><div class="eph-num"><a href="/story-chapter-1/"><span class="chapternum">Chapter 1</span></a></div></li>
<li class="locked" data-num="3"><div class="eph-num"><a href="/story-chapter-3/">Chapter 3</a></div></li>
</ul>'''
IMAGES = ['https://i0.wp.com/i.imgur.com/second.jpg', 'https://i1.wp.com/i.imgur.com/first.jpg']


def reader(**overrides):
    data = dict(protected=False, is_novel=False, defaultSource="Server 2", sources=[
        {"source": "Server 1", "images": ["https://i.imgur.com/unused.jpg"]},
        {"source": "Server 2", "images": IMAGES}])
    data.update(overrides)
    return ('<div class="allc"><a href="/manga/story/">All chapters</a></div>'
            '<div id="readerarea"></div><script>ts_reader.run(' + json.dumps(data) + ');</script>')


class FakeTransport:
    def __init__(self):
        self.calls = []
        self.documents = {"/manga/": CATALOG, "/": CATALOG, "/page/2/": CATALOG,
                          "/manga/story/": SERIES, "/story-chapter-2-5/": reader()}

    def get(self, url, *, image=False):
        self.calls.append(url)
        if image:
            return b"image-bytes", "image/jpeg"
        return self.documents[urlsplit(url).path].encode(), "text/html"


@pytest.fixture(autouse=True)
def no_network(monkeypatch):
    def blocked(*args, **kwargs):
        raise AssertionError("Unexpected network call")
    monkeypatch.setattr(requests.sessions.Session, "request", blocked)


@pytest.fixture
def service():
    return Qiscans(FakeTransport())


def test_catalog_excludes_novels_sidebar_and_has_explicit_pagination(service):
    result = service.listing()
    assert [r['id'] for r in result['results']] == ['story']
    assert result['results'][0]['title'] == 'Story & Friends'
    assert result['total'] is None and result['has_next'] is True
    service.listing(2)
    assert parse_qs(urlsplit(service.transport.calls[-1]).query)['page'] == ['2']


def test_catalog_cache_isolated_from_callers(service):
    service.listing()['results'].clear()
    assert len(service.listing()['results']) == 1
    assert len(service.transport.calls) == 1


def test_search_pagination_and_query_encoding(service):
    service.listing(2, query='Story & Friends')
    url = urlsplit(service.transport.calls[-1])
    assert url.path == '/page/2/'
    assert parse_qs(url.query)['s'] == ['Story & Friends']


def test_tags_and_combined_search_filter_are_enforced(service):
    assert service.tags() == [{'id': '3', 'name': 'Action'}, {'id': '4', 'name': 'Sports'}]
    assert service.listing(query='Story', tag='4')['results'] == []
    assert len(service.listing(query='Story', tag='3')['results']) == 1
    with pytest.raises(SourceError, match='Gênero'):
        service.listing(tag='unknown')


def test_genre_forwarded_to_catalog(service):
    service.listing(tag='3')
    assert parse_qs(urlsplit(service.transport.calls[-1]).query)['genre[]'] == ['3']


def test_empty_results_distinct_from_blocked_or_broken_html(service):
    service.transport.documents['/'] = '<div class="postbody"><div class="listupd"><center><h3>Not Found</h3></center></div></div>'
    assert service.listing(query='absent')['results'] == []
    service.transport.documents['/'] = '<html>Please verify you are human</html>'
    with pytest.raises(SourceError):
        service.listing(query='blocked')
    service.transport.documents['/'] = '<div class="postbody"><div class="listupd"></div></div>'
    with pytest.raises(SourceError):
        service.listing(query='broken')


def test_series_metadata_fractional_numbers_and_locked_chapters(service):
    data = service.info('story')
    assert data['year'] == 2024 and data['authors'] == ['Example Author']
    assert data['description'] == 'First paragraph. Second paragraph.'
    assert data['tagLinks'] == [{'id': '3', 'name': 'Action'}]
    chapters = service.chapters('story')['chapters']
    assert [c['number'] for c in chapters] == ['2.5', '1']
    assert all(c['lang'] == 'en' for c in chapters)
    assert sum('/manga/story/' in u for u in service.transport.calls) == 1


def test_reader_uses_selected_server_and_preserves_page_order(service):
    assert service.pages('story', 'story-chapter-2-5')['pages'] == IMAGES


def test_chapter_must_belong_to_series(service):
    with pytest.raises(SourceError) as exc:
        service.pages('story', 'other-chapter')
    assert exc.value.status == 404
    assert not any('/other-chapter/' in u for u in service.transport.calls)


def test_reader_parent_is_checked(service):
    service.transport.documents['/story-chapter-2-5/'] = reader().replace('/manga/story/', '/manga/other/')
    with pytest.raises(SourceError, match='outra obra'):
        service.pages('story', 'story-chapter-2-5')


@pytest.mark.parametrize('values', [dict(protected=True), dict(is_novel=True), dict(protected=None),
                                  dict(sources=[]), dict(sources=[{'images': []}]),
                                  dict(sources=[{'images': ['https://evil.example/image.jpg']}])])
def test_reader_rejects_unavailable_and_unsafe_data(service, values):
    service.transport.documents['/story-chapter-2-5/'] = reader(**values)
    with pytest.raises(SourceError):
        service.pages('story', 'story-chapter-2-5')


@pytest.mark.parametrize('url', ['http://i.imgur.com/a.jpg', 'https://i.imgur.com.evil.example/a',
                              'https://localhost/a', 'https://127.0.0.1/a',
                              'https://user:password@i.imgur.com/a', 'https://i.imgur.com:8080/a',
                              '//i.imgur.com/a', 'file:///etc/passwd'])
def test_proxy_url_validation(url):
    with pytest.raises(SourceError):
        checked_url(url, image=True)


class FakeResponse:
    def __init__(self, status=200, headers=None, body=b'<html></html>'):
        self.status_code = status
        self.headers = headers or {'Content-Type': 'text/html'}
        self.body = body

    def __enter__(self):
        return self

    def __exit__(self, *args):
        pass

    def iter_content(self, size):
        yield self.body


def test_redirect_to_imgur_and_content_type(monkeypatch):
    responses = iter([FakeResponse(302, {'Location': 'https://i.imgur.com/a.jpg'}),
                      FakeResponse(headers={'Content-Type': 'image/jpeg'}, body=b'jpeg')])
    monkeypatch.setattr(requests, 'get', lambda *a, **kw: next(responses))
    assert Transport().get('https://i0.wp.com/i.imgur.com/a.jpg', image=True) == (b'jpeg', 'image/jpeg')


def test_redirect_cannot_reach_internal_host(monkeypatch):
    calls = []
    def get(url, **kwargs):
        calls.append(url)
        return FakeResponse(302, {'Location': 'http://127.0.0.1/secret'})
    monkeypatch.setattr(requests, 'get', get)
    with pytest.raises(SourceError):
        Transport().get('https://i.imgur.com/a.jpg', image=True)
    assert len(calls) == 1


def test_429_sets_cooldown_and_api_retry_header(service, monkeypatch):
    calls = []
    def get(*args, **kwargs):
        calls.append(1)
        return FakeResponse(429, {'Retry-After': '120'})
    monkeypatch.setattr(requests, 'get', get)
    service.transport = Transport()
    client = create_app(service).test_client()
    first = client.get('/api/manga/catalog')
    second = client.get('/api/manga/search?q=another')
    assert first.status_code == second.status_code == 503
    assert first.headers['Retry-After'] == '120'
    assert len(calls) == 1


def test_html_is_not_served_as_an_image(monkeypatch):
    monkeypatch.setattr(requests, 'get', lambda *a, **kw: FakeResponse())
    with pytest.raises(SourceError, match='Tipo'):
        Transport().get('https://i.imgur.com/a.jpg', image=True)


def test_bounded_download(monkeypatch):
    monkeypatch.setattr(requests, 'get', lambda *a, **kw: FakeResponse(body=b'x' * (4 * 1024 * 1024 + 1)))
    with pytest.raises(SourceError, match='tamanho'):
        Transport().get(BASE_URL)


def test_api_contract_and_invalid_arguments(service):
    client = create_app(service).test_client()
    for route in ['/api/health', '/api/manga/catalog', '/api/manga/search?q=Story',
                  '/api/manga/tags', '/api/manga/story', '/api/manga/story/chapters',
                  '/api/manga/story/chapters/story-chapter-2-5/pages']:
        response = client.get(route)
        assert response.status_code == 200, response.json
        assert response.json['source'] == 'qiscans'
    for suffix in ['page=0', 'page=501', 'page=NaN', 'source=asura', 'q=' + 'x' * 121]:
        assert client.get('/api/manga/catalog?' + suffix).status_code == 400
    assert client.get('/api/manga/story%3Fbad').status_code == 400
    response = client.get('/api/proxy/image?url=https://i.imgur.com/a.jpg')
    assert response.data == b'image-bytes' and response.content_type == 'image/jpeg'
    assert client.get('/api/proxy/image?url=http://localhost').status_code == 400
