"""Load independent outreach and search bounds before opening the browser."""
import json
from pathlib import Path
from urllib.parse import urlencode


def validate_run_bounds(max_attempts, max_pages, max_candidates):
    for key, value in dict(max_attempts=max_attempts, max_pages=max_pages, max_candidates=max_candidates).items():
        if type(value) is not int or value < 1:
            raise ValueError('%s must be a positive integer' % key)


def build_search_url(keywords, network):
    if not isinstance(keywords, str) or not keywords.strip():
        raise ValueError('keywords must be a nonempty string')
    if (not isinstance(network, list) or not network
            or any(not isinstance(value, str) or value not in {'F', 'S', 'O'} for value in network)
            or len(set(network)) != len(network)):
        raise ValueError('network must be a nonempty list of unique F, S or O values')
    return 'https://www.linkedin.com/search/results/people/?' + urlencode({'keywords': keywords, 'network': json.dumps(network)})


def validate_run_config(settings):
    keys = {'keywords', 'network', 'max_attempts', 'max_pages', 'max_candidates',
            'login_timeout_seconds', 'profile_timeout_seconds'}
    if not isinstance(settings, dict) or set(settings) != keys:
        raise ValueError('Run config must contain exactly: ' + ', '.join(sorted(keys)))
    validate_run_bounds(settings['max_attempts'], settings['max_pages'], settings['max_candidates'])
    build_search_url(settings['keywords'], settings['network'])
    for key, minimum in [('login_timeout_seconds', 1), ('profile_timeout_seconds', 0)]:
        if type(settings[key]) is not int or not minimum <= settings[key] <= 600:
            raise ValueError('%s must be an integer between %s and 600' % (key, minimum))
    return settings


def load_run_config(path):
    return validate_run_config(json.loads(Path(path).read_text(encoding='utf-8')))
