"""Recover the Steam search field when the Deck's left pad moves Steam down a row."""
import json

from inline_state import _observe, _result, verified_request


def search_identity(field):
    if not isinstance(field, tuple) or len(field) != 2:
        return False
    owner, key = field
    if type(owner) is not int or not isinstance(key, tuple):
        return False
    if len(key) == 6 and key[0] == 'atspi':
        return (type(key[1]) is int and key[1] == owner and type(key[2]) is int and key[2] > 0
                and isinstance(key[3], str) and key[3].startswith(':')
                and all(isinstance(path, str) and path.startswith('/') for path in key[4:]))
    if len(key) != 4:
        return False
    identifier, epoch, kind, path = key
    return (kind == "search" and isinstance(identifier, str) and isinstance(path, tuple)
            and _result(identifier, ["editor", epoch, kind, list(path)]) is not None)


# Read no query text. Only the original editor and the directly adjacent search
# category tab are eligible; intentional navigation elsewhere belongs to Steam.
_FOCUS = r"""(() => {
    if (self.top !== self || self.origin !== 'https://steamloopback.host' ||
        location.origin !== self.origin || new URL(document.baseURI).origin !== self.origin ||
        !document.hasFocus() || document.visibilityState !== 'visible' ||
        Math.round(performance.timeOrigin) !== EPOCH) return 'blocked';
    const visible = e => e.checkVisibility({checkVisibilityCSS: true}) &&
        e.getBoundingClientRect().width > 2 && e.getBoundingClientRect().height > 2;
    const css = n => '.' + CSS.escape(n);
    if (Array.from(document.querySelectorAll(BLOCKERS)).some(visible)) return 'blocked';
    let field = document.documentElement;
    for (const index of PATH.toReversed()) field = field?.children[index];
    if (!field || !field.matches('input[type="search"]') || !field.classList.contains(SEARCH) ||
        field.disabled || field.readOnly || !visible(field) ||
        field.closest('[inert],[aria-hidden="true"],[aria-disabled="true"]') ||
        /password|one-time-code|cc-/.test(field.autocomplete || '') ||
        field.form?.querySelector('input[type="password"]')) return 'blocked';
    if (document.activeElement === field) return 'focused';
    if (!/^\/routes\/search(?:\/|$)/.test(location.pathname) ||
        Array.from(document.querySelectorAll(css(SEARCH))).filter(visible).length !== 1)
        return 'blocked';
    const tab = document.activeElement;
    const tabs = Array.from(document.querySelectorAll('[role="tablist"]')).filter(visible);
    const list = tabs.find(el => {
        const f = field.getBoundingClientRect(), r = el.getBoundingClientRect();
        return Math.abs(r.top-f.bottom) <= f.height && r.left < f.right && r.right > f.left;
    });
    if (!list || !visible(tab)) return 'blocked';
    const neighbor = tab.matches('[role="tab"]') && tab.closest('[role="tablist"]') === list;
    // Only a continuing held Space can match a Steam search result after
    // repeated Down. A fresh tap must never take focus from a result.
    const grid = ALLOW_RESULT && RESULT_CLASS && GRID_CLASS &&
        tab.matches('div[role="link"]' + css(RESULT_CLASS)) && tab.closest('[role="gridcell"]')
        ?.closest('[role="row"]')?.closest('[role="grid"]')?.closest(css(GRID_CLASS));
    const results = grid && grid.closest('[role="tabpanel"]');
    if (!neighbor && !(results && visible(results) &&
        Math.abs(results.getBoundingClientRect().top - field.getBoundingClientRect().bottom) <=
            field.getBoundingClientRect().height)) return 'blocked';
    const f = field.getBoundingClientRect(), r = list.getBoundingClientRect();
    if (f.top < -2 || f.bottom > innerHeight / 5 || Math.abs(r.top - f.bottom) > f.height ||
        r.left >= f.right || r.right <= f.left || tab.closest('[inert],[aria-hidden="true"]'))
        return 'blocked';
    field.focus({preventScroll:true});
    return document.activeElement === field ? 'restored' : 'blocked';
})()"""


def recover(field, *, held=False):
    if not search_identity(field):
        return None
    if field[1][0] == 'atspi' and len(field[1]) == 6:
        from accessible_inline import recover_space
        return recover_space(field[1], held=held)
    xid, (identifier, epoch, _, path) = field

    def operation(skin):
        blockers = ','.join(['dialog', '[role="dialog"]', '[aria-modal="true"]', 'input[type="password"]',
                             *('.' + name for name in (skin.modal, skin.standalone, skin.buffer,
                                                       skin.keyboard, skin.positioner))])
        expr = (_FOCUS.replace('ALLOW_RESULT', json.dumps(held)).replace('RESULT_CLASS', json.dumps(skin.search_result))
                .replace('GRID_CLASS', json.dumps(skin.search_grid)).replace('EPOCH', str(epoch)).replace('PATH', json.dumps(path))
                .replace('BLOCKERS', json.dumps(blockers)).replace('SEARCH', json.dumps(skin.search)))
        return _observe(skin, page_id=identifier, expression=expr)

    return verified_request(xid, operation)
