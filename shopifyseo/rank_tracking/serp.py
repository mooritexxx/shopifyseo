"""SerpApi adapter: fixed context, offset-aware ranks, no secrets in errors."""
import json
import time
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

import requests

PROFILE = dict(domain='vapely.ca', google_domain='google.ca', gl='ca', hl='en',
               location='Toronto, Ontario, Canada', device='desktop')
PROFILE_JSON = json.dumps(PROFILE, sort_keys=True)


class RankError(ValueError):
    pass


class RankCancelled(RankError):
    pass


def clean_url(value):
    try:
        p = urlsplit(value)
        if p.scheme not in ('http', 'https') or not p.hostname or p.username or p.password:
            return ''
        query = [(k, v) for k, v in parse_qsl(p.query, keep_blank_values=True)
                 if not k.lower().startswith('utm_') and k.lower() not in ('srsltid', 'gclid', 'fbclid')]
        return urlunsplit((p.scheme.lower(), p.netloc.lower(), p.path, urlencode(query), ''))
    except ValueError:
        return ''


def host(url):
    return (urlsplit(clean_url(url)).hostname or '').removeprefix('www.').rstrip('.')


def is_target(url):
    h = host(url)
    return h == PROFILE['domain'] or h.endswith('.' + PROFILE['domain'])


def url_identity(url):
    p = urlsplit(clean_url(url))
    return (host(url), p.path.rstrip('/') or '/', p.query)


def remaining_credits(key):
    if not key:
        raise RankError('Set your SerpApi key in Settings before checking rankings.')
    try:
        response = requests.get('https://serpapi.com/account.json', params={'api_key': key}, timeout=20)
        response.raise_for_status()
        data = response.json()
        # SerpApi distinguishes plan balance from extra purchased credits.
        if data.get('error'):
            raise ValueError()
        plan = data.get('plan_searches_left')
        extra = data.get('extra_credits', 0)
        if plan is None and 'total_searches_left' not in data:
            raise ValueError()
        return max(0, int(data.get('total_searches_left', int(plan or 0) + int(extra or 0))))
    except Exception:
        raise RankError('Could not verify SerpApi credits. Check your key and connection.') from None


def request_page(key, params, before_request):
    for attempt in range(2):
        before_request()  # durable accounting before dispatch, including ambiguous timeouts
        try:
            res = requests.get('https://serpapi.com/search.json',
                               params={**params, 'api_key': key}, timeout=90)
        except (requests.Timeout, requests.ConnectionError):
            if attempt == 0:
                time.sleep(1)
                continue
            raise RankError('SerpApi timed out or could not connect after one retry.') from None
        except requests.RequestException:
            raise RankError('SerpApi request failed.') from None
        if res.status_code == 429 and attempt == 0:
            time.sleep(1)
            continue
        if res.status_code != 200:
            raise RankError(f'SerpApi returned HTTP {res.status_code}.')
        try:
            data = res.json()
        except ValueError:
            raise RankError('SerpApi returned invalid JSON.') from None
        if not isinstance(data, dict) or data.get('error'):
            raise RankError('SerpApi could not complete this search.')
        return data
    raise RankError('SerpApi rate limit reached.')


def check_term(term, max_pages, key, before_request):
    result = dict(position=None, ranking_url=None, pages_checked=0, checked_depth=0,
                  top1_domain=None, top2_domain=None, top3_domain=None, status='ok', error=None,
                  coverage_complete=True, cancelled=False)
    seen_pages = set()
    incomplete = False
    try:
        for page in range(max_pages):
            start = page * 10
            params = {k: v for k, v in PROFILE.items() if k != 'domain'}
            data = request_page(key, {**params, 'engine': 'google', 'q': term,
                                     'start': start, 'num': 10, 'no_cache': 'true'}, before_request)
            echoed = data.get('search_parameters', {})
            if int(echoed.get('start', 0)) != start:
                raise RankError('Search returned an unexpected page offset; rank is unknown.')
            organic = data.get('organic_results')
            if not isinstance(organic, list) or not organic:
                raise RankError('No organic results returned; rank is unknown.')
            positions = [r.get('position') for r in organic if isinstance(r, dict)]
            if len(positions) != len(organic) or any(type(p) is not int or not 1 <= p <= 10 for p in positions) or len(set(positions)) != len(positions):
                raise RankError('Unexpected organic positions; rank is unknown.')
            links = tuple(clean_url(r.get('link', '')) for r in organic)
            if not all(links) or links in seen_pages:
                raise RankError('Invalid or repeated results page; rank is unknown.')
            seen_pages.add(links)
            result['pages_checked'] += 1
            # Google can return fewer than ten organic results on a valid page.
            # Keep page coverage separate from a verified top-N absence.
            if set(positions) != set(range(1, 11)):
                incomplete = True
                result['coverage_complete'] = False
            if page == 0:
                domains = list(dict.fromkeys(host(link) for link in links))[:3]
                result.update({f'top{i+1}_domain': d for i, d in enumerate(domains)})
            hits = [(start + r['position'], clean_url(r['link'])) for r in organic if is_target(r['link'])]
            if hits:
                result['position'], result['ranking_url'] = min(hits)
                result['checked_depth'] = start + max(positions)
                return result
            if not incomplete:
                result['checked_depth'] = start + 10
    except RankCancelled:
        result.update(status='error', cancelled=True, error='Check stopped by user; rank is unknown.')
    except (RankError, TypeError, ValueError) as exc:
        result.update(status='error', position=None, ranking_url=None,
                      error=str(exc) if isinstance(exc, RankError) else 'Unexpected search response; rank is unknown.')
    return result
