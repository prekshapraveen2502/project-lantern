"""Shared pytest options.

--no-scope-filter (Part 3 item 3): test-only switch that removes the store's scope filter inside
tests/test_retrieval.py, to show the isolation test fails without it (the test has teeth). src/retrieve.py has no
bypass; the switch replaces its scope_filter in the test process only.
"""


def pytest_addoption(parser):
    parser.addoption("--no-scope-filter", action="store_true", default=False,
                     help="remove the retrieval scope filter in tests/test_retrieval.py (documented failing run)")
