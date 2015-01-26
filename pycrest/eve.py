import base64
import requests
import time
from pycrest import version
from pycrest.compat import bytes_, text_
from pycrest.errors import APIException
from pycrest.weak_ciphers import WeakCiphersAdapter

try:
    from urllib.parse import quote
except ImportError:  # pragma: no cover
    from urllib import quote
import logging

logger = logging.getLogger("pycrest.eve")


class APIConnection(object):
    def __init__(self, additional_headers=None, user_agent=None, cache_time=600):
        self.cache_time = cache_time
        # Set up a Requests Session
        session = requests.Session()
        if additional_headers is None:
            additional_headers = {}
        if user_agent is None:
            user_agent = "PyCrest/{0}".format(version)
        session.headers.update({
            "User-Agent": user_agent,
            "Accept": "application/json",
        })
        session.headers.update(additional_headers)
        session.mount('https://public-crest.eveonline.com',
                WeakCiphersAdapter())
        self._session = session

    def get(self, resource, params=None):
        logger.debug('Getting resource %s', resource)
        if params is None:
            params = {}
        res = self._session.get(resource, params=params)
        if res.status_code != 200:
            raise APIException("Got unexpected status code from server: %i" % res.status_code)
        return res.json()


class EVE(APIConnection):
    def __init__(self, **kwargs):
        self.api_key = kwargs.pop('api_key', None)
        self.client_id = kwargs.pop('client_id', None)
        self.redirect_uri = kwargs.pop('redirect_uri', None)
        if kwargs.pop('testing', False):
            self._public_endpoint = "http://public-crest-sisi.testeveonline.com/"
            self._authed_endpoint = "https://api-sisi.testeveonline.com/"
            self._image_server = "https://image.testeveonline.com/"
            self._oauth_endpoint = "https://sisilogin.testeveonline.com/oauth"
        else:
            self._public_endpoint = "https://public-crest.eveonline.com/"
            self._authed_endpoint = "https://crest-tq.eveonline.com/"
            self._image_server = "https://image.eveonline.com/"
            self._oauth_endpoint = "https://login.eveonline.com/oauth"
        self._endpoint = self._public_endpoint
        self._cache = {}
        self._data = None

        APIConnection.__init__(self, cache_time=kwargs.pop('cache_time', 600),
                **kwargs)

    def __call__(self):
        if not self._data:
            self._data = APIObject(self.get(self._endpoint), self)
        return self._data

    def __getattr__(self, item):
        return self._data.__getattr__(item)

    def auth_uri(self, scopes=None, state=None):
        s = [] if not scopes else scopes
        return "%s/authorize?response_type=code&redirect_uri=%s&client_id=%s%s%s" % (
            self._oauth_endpoint,
            quote(self.redirect_uri, safe=''),
            self.client_id,
            "&scope=%s" % ','.join(s) if scopes else '',
            "&state=%s" % state if state else ''
        )

    def authorize(self, code):
        auth = text_(base64.b64encode(bytes_("%s:%s" % (self.client_id, self.api_key))))
        headers = {"Authorization": "Basic %s" % auth}
        params = {"grant_type": "authorization_code", "code": code}
        res = self._session.post("%s/token" % self._oauth_endpoint, params=params, headers=headers)
        if res.status_code != 200:
            raise APIException("Got unexpected status code from API: %i" % res.status_code)
        return AuthedConnection(res.json(), self._authed_endpoint, self._oauth_endpoint, self.client_id, self.api_key)


class AuthedConnection(EVE):
    def __init__(self, res, endpoint, oauth_endpoint, client_id=None, api_key=None, **kwargs):
        EVE.__init__(self, **kwargs)
        self.client_id = client_id
        self.api_key = api_key
        self.token = res['access_token']
        self.refresh_token = res['refresh_token']
        self.expires = round(time.time()) + res['expires_in']
        self._oauth_endpoint = oauth_endpoint
        self._endpoint = endpoint
        self._session.headers.update(
                {"Authorization": "Bearer %s" % self.token})

    def whoami(self):
        if 'whoami' not in self._cache:
            self._cache['whoami'] = self.get("https://login.eveonline.com/oauth/verify")
        return self._cache['whoami']

    def refresh(self):
        auth = text_(base64.b64encode(bytes_("%s:%s" % (self.client_id, self.api_key))))
        headers = {"Authorization": "Basic %s" % auth}
        params = {"grant_type": "refresh_token", "refresh_token": self.refresh_token}
        res = self._session.post("%s/token" % self._oauth_endpoint, params=params, headers=headers)
        if res.status_code != 200:
            raise APIException("Got unexpected status code from API: %i" % res.status_code)
        return AuthedConnection(res.json(), self._endpoint, self._oauth_endpoint, self.client_id, self.api_key)


class APIObject(object):
    def __init__(self, parent, connection):
        self._dict = {}
        self.connection = connection
        self._cache = None
        for k, v in parent.items():
            if type(v) is dict:
                self._dict[k] = APIObject(v, connection)
            elif type(v) is list:
                self._dict[k] = self._wrap_list(v)
            else:
                self._dict[k] = v

    def _wrap_list(self, list_):
        new = []
        for item in list_:
            if type(item) is dict:
                new.append(APIObject(item, self.connection))
            elif type(item) is list:
                new.append(self._wrap_list(item))
            else:
                new.append(item)
        return new

    def __getattr__(self, item):
        return self._dict[item]

    def __call__(self, *args, **kwargs):
        if ((not self._cache) or round(time.time()) - self._cache[0] > self.connection.cache_time) and 'href' in self._dict:
            logger.debug("%s not yet loaded", self._dict['href'])
            self._cache = (round(time.time()), APIObject(self.connection.get(self._dict['href']), self.connection))
            return self._cache[1]
        elif self._cache:
            return self._cache[1]
        else:
            return self

    def __str__(self):  # pragma: no cover
        return self._dict.__str__()

    def __repr__(self):  # pragma: no cover
        return self._dict.__repr__()