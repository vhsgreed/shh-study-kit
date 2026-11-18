"""Offline tests for the login helpers. The browser walk itself is tested live only."""
import os
import re

from ladok_vakt import core, login as L


def _cfg(entity):
    os.environ['LADOK_IDP_ENTITY'] = entity
    try:
        return core.Config()
    finally:
        del os.environ['LADOK_IDP_ENTITY']


def test_idp_domain_from_entity():
    assert L.idp_domain(_cfg('https://swamid2.shh.se/idp/shibboleth')) == 'shh.se'
    assert L.idp_domain(_cfg('https://login.kth.se/idp/shibboleth')) == 'kth.se'


def test_idp_domain_override(monkeypatch):
    monkeypatch.setenv('LADOK_IDP_DOMAIN', 'KI.SE')
    assert L.idp_domain(_cfg('https://whatever.example/idp')) == 'ki.se'


def test_pick_idp_js_embeds_domain_as_json_string():
    js = L.PICK_IDP_JS % '"shh.se"'
    assert '("shh.se")' in js and '#searchinput' in js


def test_authed_js_requires_a_json_body():
    # Logged out, the proxy answers 200 with HTML: status alone must not count.
    js = L.AUTHED_JS % '/x'
    assert 'r.text()' in js and "'html'" in js


def test_login_tries_picker_then_direct(monkeypatch):
    seen = []

    async def fake(cfg, route='picker', timeout=45.0):
        seen.append(route)
        if route == 'picker':
            raise core.Fault('picker broke')
        return 'ok'

    monkeypatch.setattr(L, '_login', fake)
    assert L.login(_cfg('https://swamid2.shh.se/idp/shibboleth')) == 'ok (direct)'
    assert seen == ['picker', 'direct']


def test_login_reports_both_failures(monkeypatch):
    async def fake(cfg, route='picker', timeout=45.0):
        raise core.Fault('%s broke' % route)

    monkeypatch.setattr(L, '_login', fake)
    try:
        L.login(_cfg('https://swamid2.shh.se/idp/shibboleth'))
    except core.Fault as exc:
        assert re.search('picker: picker broke .* direct: direct broke', str(exc))
    else:
        raise AssertionError('expected Fault')
