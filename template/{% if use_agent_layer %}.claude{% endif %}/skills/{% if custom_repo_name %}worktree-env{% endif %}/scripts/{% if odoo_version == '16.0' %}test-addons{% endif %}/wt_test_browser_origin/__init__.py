from odoo.tests.common import ChromeBrowser
from odoo.tools import config


def post_load():
    # ChromeBrowser.clear() names the origin as the bare HOST, which Chrome 158 rejects as no URL.
    # Remove this module, and its --load in wt-test.sh, once Odoo 16.0's clear()
    # (odoo/tests/common.py) sends Storage.clearDataForOrigin a URL as the origin.
    websocket_request = ChromeBrowser._websocket_request

    def _websocket_request(self, method, *, params=None, timeout=10.0):
        if method == "Storage.clearDataForOrigin" and "://" not in params["origin"]:
            params = dict(params, origin=f"http://{params['origin']}:{config['http_port']}")
        return websocket_request(self, method, params=params, timeout=timeout)

    ChromeBrowser._websocket_request = _websocket_request
