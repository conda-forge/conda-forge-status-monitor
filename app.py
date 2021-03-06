import datetime
import pytz
import dateutil.parser
from ruamel.yaml import YAML
from ruamel.yaml.compat import StringIO
import requests
import json
import hmac
import hashlib
import os

import lxml.html
import cachetools

from flask import (
    Flask, request, make_response, jsonify, render_template)

APP_DATA = {
    'azure-pipelines': {
        'repos': cachetools.LRUCache(maxsize=128),
        'rates': cachetools.LRUCache(maxsize=96),
    },
    'travis-ci': {
        'repos': cachetools.LRUCache(maxsize=128),
        'rates': cachetools.LRUCache(maxsize=96),
    },
    'github-actions': {
        'repos': cachetools.LRUCache(maxsize=128),
        'rates': cachetools.LRUCache(maxsize=96),
    },
    'appveyor': {
        'repos': cachetools.LRUCache(maxsize=128),
        'rates': cachetools.LRUCache(maxsize=96),
    },
    'circleci': {
        'repos': cachetools.LRUCache(maxsize=128),
        'rates': cachetools.LRUCache(maxsize=96),
    },
    'drone': {
        'repos': cachetools.LRUCache(maxsize=128),
        'rates': cachetools.LRUCache(maxsize=96),
    },
}

STATUS_UPDATE_DELAY = 60
NOSTATUS = 'No Status Available'
WEBS_STATUS_UPDATED = None
WEBS_STATUS_DATA = {
    'status': NOSTATUS,
    'updated_at': None,
}
START_TIME = datetime.datetime.fromisoformat("2020-01-01T00:00:00+00:00")
TIME_INTERVAL = 60*5  # five minutes


app = Flask(__name__)


def _make_time_key(uptime):
    dt = uptime.timestamp() - START_TIME.timestamp()
    return int(dt // TIME_INTERVAL)


# reload the cache
RELOAD_CACHE = True


def _reload_cache():
    print(" ")
    print("!!!!!!!!!!!!!! RELOADING THE CACHE !!!!!!!!!!!!!!")

    global APP_DATA

    try:
        data = requests.get(
            ("https://raw.githubusercontent.com/regro/cf-action-counter-db/"
             "master/data/latest.json")).json()
    except Exception as e:
        print(e)
        data = None

    if data is not None:
        for slug in APP_DATA:
            print('reloading data for %s' % slug)

            if slug not in data:
                continue
            else:
                _data = data[slug]

            for repo in _data['repos']:
                APP_DATA[slug]['repos'][repo] = _data['repos'][repo]

            for ts in _data['rates']:
                t = datetime.datetime.fromisoformat(ts).astimezone(pytz.UTC)
                key = _make_time_key(t)
                APP_DATA[slug]['rates'][key] = _data['rates'][ts]

            print("    reloaded %d repos" % len(APP_DATA[slug]['repos']))
            print("    reloaded %d rates" % len(APP_DATA[slug]['rates']))
    else:
        print("could not get app cache!")
    print("!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!")
    print(" ")


if RELOAD_CACHE:
    _reload_cache()
    RELOAD_CACHE = False


class MyYAML(YAML):
    """dump yaml as string rippd from docs"""
    def dump(self, data, stream=None, **kw):
        inefficient = False
        if stream is None:
            inefficient = True
            stream = StringIO()
        YAML.dump(self, data, stream, **kw)
        if inefficient:
            return stream.getvalue()


def _make_est_from_time_key(key, iso=False):
    est = pytz.timezone('US/Eastern')
    fmt = '%Y-%m-%d %H:%M:%S %Z%z'
    dt = datetime.timedelta(seconds=key * TIME_INTERVAL)
    t = dt + START_TIME
    t = t.astimezone(est)
    if iso:
        return t.isoformat()
    else:
        return t.strftime(fmt)


def _make_report_data(iso=False):
    now = datetime.datetime.utcnow().replace(tzinfo=pytz.UTC)
    know = _make_time_key(now)

    report = {}
    for key in APP_DATA:
        rates = {}
        for k in range(know, know-96, -1):
            tstr = _make_est_from_time_key(k, iso=iso)
            rates[tstr] = APP_DATA[key]['rates'].get(k, 0)

        total = sum(v for v in rates.values())

        report[key] = {
            'total': total,
            'rates': rates,
            'repos': {k: v for k, v in APP_DATA[key]['repos'].items()},
        }

    return report


@app.route('/', methods=['GET'])
def index():
    yaml = MyYAML()
    return render_template(
        'index.html',
        report=yaml.dump(_make_report_data(iso=False)),
    )


@app.route('/db', methods=['GET'])
def report():
    data = _make_report_data(iso=True)
    resp = make_response(jsonify(data))
    resp.headers['Access-Control-Allow-Origin'] = "*"
    return resp


@app.route('/report/<name>', methods=['GET'])
def report_name(name):
    data = _make_report_data(iso=True)
    resp = make_response(jsonify(data[name]))
    resp.headers['Access-Control-Allow-Origin'] = "*"
    return resp


@app.route('/status/azure', methods=['GET'])
def status_azure():
    status_data = {}

    # always update azure
    try:
        r = requests.get('https://status.dev.azure.com', timeout=2)
        if r.status_code != 200:
            status_data['azure'] = NOSTATUS
        else:
            s = json.loads(
                lxml
                .html
                .fromstring(r.content)
                .get_element_by_id('dataProviders')
                .text
            )

            def _rec_search(d):
                if isinstance(d, dict):
                    if 'health' in d and 'message' in d:
                        return d['message']
                    else:
                        for v in d.values():
                            if isinstance(v, dict):
                                val = _rec_search(v)
                                if val is not None:
                                    return val
                        return None
                else:
                    return None

            stat = _rec_search(s)

            if stat is None:
                stat = NOSTATUS

            status_data['status'] = stat
    except requests.exceptions.RequestException:
        status_data['status'] = NOSTATUS

    status_data['updated_at'] = datetime.datetime.now().astimezone(pytz.UTC)

    resp = make_response(jsonify(status_data))
    resp.headers['Access-Control-Allow-Origin'] = "*"
    return resp


@app.route('/status/webservices', methods=['GET'])
def status_webservices():
    global WEBS_STATUS_UPDATED
    global WEBS_STATUS_DATA

    do_update = False
    if WEBS_STATUS_UPDATED is None:
        do_update = True
    else:
        now = datetime.datetime.now().astimezone(pytz.UTC)
        dt = now - WEBS_STATUS_UPDATED
        # five minutes
        if dt.total_seconds() >= STATUS_UPDATE_DELAY:
            do_update = True

    if do_update:
        try:
            hash = hmac.new(
                os.environ['CF_WEBSERVICES_TOKEN'].encode('utf-8'),
                b'',
                hashlib.sha1).hexdigest()

            r = requests.post(
                (
                    'https://conda-forge.herokuapp.com'
                    '/conda-linting/org-hook'
                ),
                headers={
                    'X-GitHub-Event': 'ping',
                    'X-Hub-Signature': 'sha1=%s' % hash,
                },
                timeout=2,
            )

            if (
                r.status_code != 200 or
                r.elapsed.total_seconds() > 1 or
                r.text != 'pong'
            ):
                WEBS_STATUS_DATA['status'] = 'degraded'
            else:
                WEBS_STATUS_DATA['status'] = 'operational'
        except requests.exceptions.RequestException:
            WEBS_STATUS_DATA['status'] = 'degraded'

        WEBS_STATUS_UPDATED = datetime.datetime.now().astimezone(pytz.UTC)
        WEBS_STATUS_DATA['updated_at'] = WEBS_STATUS_UPDATED.isoformat()

    resp = make_response(jsonify(WEBS_STATUS_DATA))
    resp.headers['Access-Control-Allow-Origin'] = "*"
    return resp


def _update_status():
    global APP_DATA

    repo = request.json['repository']['full_name']

    if 'circleci' in request.json['context']:
        slug = 'circleci'
    elif 'appveyor' in request.json['context']:
        slug = 'appveyor'
    elif 'travis' in request.json['context']:
        slug = 'travis-ci'
    elif 'drone' in request.json['context']:
        slug = 'drone'
    else:
        print("context not found:", request.json['context'])
        return

    print("    repo:", repo)
    print("    app:", slug)
    print("    state:", request.json['state'])

    if request.json['state'] in ['success', 'failure', 'error']:

        print("    updated_at:", request.json['updated_at'])

        uptime = dateutil.parser.isoparse(request.json['updated_at'])
        interval = _make_time_key(uptime)
        if interval not in APP_DATA[slug]['rates']:
            APP_DATA[slug]['rates'][interval] = 0
        APP_DATA[slug]['rates'][interval] = (
            APP_DATA[slug]['rates'][interval] + 1)

        if repo not in APP_DATA[slug]['repos']:
            APP_DATA[slug]['repos'][repo] = 0
        APP_DATA[slug]['repos'][repo] = APP_DATA[slug]['repos'][repo] + 1


def _update_check_run():
    global APP_DATA

    repo = request.json['repository']['full_name']
    cs = request.json['check_run']

    print("    repo:", repo)
    print("    app:", cs['app']['slug'])
    print("    action:", request.json['action'])
    print("    status:", cs['status'])
    print("    conclusion:", cs['conclusion'])

    if (
        cs['app']['slug'] in APP_DATA and
        cs['status'] == 'completed'
    ):
        print("    completed_at:", cs['completed_at'])
        key = cs['app']['slug']

        uptime = dateutil.parser.isoparse(cs['completed_at'])
        interval = _make_time_key(uptime)
        if interval not in APP_DATA[key]['rates']:
            APP_DATA[key]['rates'][interval] = 0
        APP_DATA[key]['rates'][interval] = (
            APP_DATA[key]['rates'][interval]
            + 1
        )

        if repo not in APP_DATA[key]['repos']:
            APP_DATA[key]['repos'][repo] = 0
        APP_DATA[key]['repos'][repo] = (
            APP_DATA[key]['repos'][repo]
            + 1
        )


def _valid_request():
    our_hash = hmac.new(
        os.environ['CF_WEBSERVICES_TOKEN'].encode('utf-8'),
        request.data,
        hashlib.sha1,
    ).hexdigest()

    their_hash = request.headers.get('X-Hub-Signature').split("=")[1]

    return hmac.compare_digest(their_hash, our_hash)


@app.route('/payload', methods=['POST'])
def payload():
    if request.method == 'POST':
        if not _valid_request():
            return make_response(
                "invalid request",
                403,
            )

        event_type = request.headers.get('X-GitHub-Event')
        print(" ")
        print("event:", event_type)

        if event_type == 'ping':
            return 'pong'
        elif event_type == 'check_run':
            _update_check_run()
            return event_type
        elif event_type == 'check_suite':
            return event_type
        elif event_type == 'status':
            _update_status()
            return event_type
        else:
            return make_response(
                "could not handle event: '%s'" % event_type,
                404,
            )
