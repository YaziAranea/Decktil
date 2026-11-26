// KWin supplies cursor and window geometry; only AT-SPI verifies the focused editor.
const owner = workspace.activeWindow;
const service = "local.deck.PlasmaKeyboard";
const ticket = "@@TICKET@@";
const browser = /^(?:google[-.]chrome(?:-stable)?|chromium(?:-browser)?|firefox|org\.chromium\.Chromium|com\.google\.Chrome|org\.mozilla\.firefox|brave-browser)$/i;
function request(method, ...args) {
    callDBus(service, "/BrowserFieldReturn", service, method, ticket, ...args);
}
if (!owner || !(browser.test(String(owner.resourceClass)) || browser.test(String(owner.desktopFileName)))) {
    request("End");
} else {
    let valid = true;
    let bounds = null;
    let previous = null;
    let initial = null;
    let sequence = 0;
    let pending = false;

    function stop() {
        if (valid) {
            valid = false;
            request("End");
        }
    }
    function pointer() {
        const frame = owner.clientGeometry;
        return [Math.floor(workspace.cursorPos.x - frame.x), Math.floor(workspace.cursorPos.y - frame.y)];
    }
    function contains(rect, x, y) {
        const [l, t, w, h] = rect;
        return x >= l && x < l + w && y >= t && y < t + h;
    }
    function check(x, y) {
        const crossing = sequence;
        pending = true;
        request("FieldAt", owner.pid, x, y, function(answer) {
            pending = false;
            if (!valid || workspace.activeWindow !== owner || !previous) return;
            const [cx, cy] = pointer();
            if (crossing !== sequence) {
                // The pointer left and re-entered during the previous lookup.
                check(cx, cy);
                return;
            }
            if (answer && contains(String(answer).split(" ").map(Number), cx, cy)) request("Open");
        });
    }
    function moved() {
        if (!valid || workspace.activeWindow !== owner) return;
        const [x, y] = pointer();
        if (!bounds) {
            // Keep only the extrema so a fast return while AT-SPI starts is seen.
            if (!initial) initial = [x, y, x, y];
            else initial = [Math.min(x, initial[0]), Math.min(y, initial[1]),
                            Math.max(x, initial[2]), Math.max(y, initial[3])];
            return;
        }
        const inside = contains(bounds, x, y);
        const entered = previous === false && inside;
        if (previous !== inside) ++sequence;
        previous = inside;
        if (entered && !pending) check(x, y);
    }
    workspace.windowActivated.connect(w => { if (w !== owner) stop(); });
    workspace.cursorPosChanged.connect(moved);
    moved();
    request("FieldAt", owner.pid, -1, -1, function(answer) {
        if (!answer || !valid) { stop(); return; }
        bounds = String(answer).split(" ").map(Number);
        previous = contains(bounds, initial[0], initial[1]) && contains(bounds, initial[2], initial[3]);
        moved();
    });
}
