"""Explicit live smoke check; never run as part of the offline test suite.

Run from the repository root: python scripts/smoke.py
Fetches a small sample of public pages and one image into memory.
"""
import json
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from qiscans.app import create_app


def main():
    client = create_app().test_client()

    def get(path, **params):
        response = client.get(path, query_string=params)
        if response.status_code != 200:
            raise RuntimeError(f'{path}: HTTP {response.status_code}: {response.json}')
        return response.json

    catalog = get('/api/manga/catalog')
    following = get('/api/manga/catalog', page=2)
    assert catalog['total'] == following['total']
    assert catalog['total_pages'] == max(1, (catalog['total'] + 19) // 20)
    tags = get('/api/manga/tags')['tags']
    search = get('/api/manga/search', q='kingdom')
    identifier = next(r['id'] for r in search['results'] if r['id'] == 'kingdom')
    details = get('/api/manga/' + identifier)
    chapters = get('/api/manga/' + identifier + '/chapters')['chapters']
    pages = get('/api/manga/' + identifier + '/chapters/' + chapters[0]['id'] + '/pages')['pages']
    image = client.get('/api/proxy/image', query_string={'url': pages[0]})
    if image.status_code != 200 or not image.content_type.startswith('image/') or not image.data:
        raise RuntimeError(f'Image: HTTP {image.status_code}: {image.json}')
    second_id = next(r['id'] for r in catalog['results'] if r['id'] != identifier)
    second = get('/api/manga/' + second_id)
    broad = get('/api/manga/search', q='the')
    if not broad['has_next']:
        raise RuntimeError('A busca de amostra não oferece uma segunda página para validar.')
    broad_next = get('/api/manga/search', q='the', page=2)
    if [r['id'] for r in broad['results']] == [r['id'] for r in broad_next['results']]:
        raise RuntimeError('A paginação da busca repetiu os mesmos resultados.')
    sports = next(t for t in tags if t['name'] == 'Sports')
    filtered = get('/api/manga/catalog', tag=sports['id'])
    filtered_info = get('/api/manga/' + filtered['results'][0]['id'])
    if sports['id'] not in {t['id'] for t in filtered_info['tagLinks']}:
        raise RuntimeError('O catálogo não respeitou o filtro por gênero.')
    print(json.dumps({'catalog_items': len(catalog['results']), 'total': catalog['total'],
                      'total_pages': catalog['total_pages'], 'page_2_items': len(following['results']),
                      'tags': len(tags), 'search_results': len(search['results']),
                      'title': details['title'], 'second_title': second['title'],
                      'search_page_2_items': len(broad_next['results']),
                      'genre_items': len(filtered['results']),
                      'chapters': len(chapters), 'pages': len(pages),
                      'image_type': image.content_type, 'image_bytes': len(image.data)}, ensure_ascii=False))


if __name__ == '__main__':
    main()
