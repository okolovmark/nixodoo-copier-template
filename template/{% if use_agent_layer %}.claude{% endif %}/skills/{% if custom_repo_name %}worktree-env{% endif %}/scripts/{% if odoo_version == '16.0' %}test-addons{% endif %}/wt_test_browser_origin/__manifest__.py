{
    "name": "Test browser storage origin",
    "version": "16.0.1.0.0",
    "summary": "wt-test.sh runs only: a URL for the origin whose storage the test browser clears",
    "license": "LGPL-3",
    # loaded server-wide by wt-test.sh; a test run's module list update records it, never to install
    "installable": False,
    "post_load": "post_load",
}
