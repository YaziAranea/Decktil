// Loaded by the desktop input bridge for the lifetime of its Desktop Mode session.
function watch(window) {
    if (String(window.resourceClass).toLowerCase() !== "steam" ||
        String(window.resourceName).toLowerCase() !== "steamwebhelper")
        return;
    function suppressKeyboard() {
        if (window.caption !== "Steam Input On-screen Keyboard" &&
            window.caption !== "Экранная клавиатура системы ввода Steam")
            return;
        // Steam may map its own keyboard before the bridge receives the
        // controller focus change that unloads this script in Big Picture.
        const foreground = workspace.activeWindow;
        const owner = window.transientFor;
        if ([foreground, owner].some(w => w && w.fullScreen &&
                String(w.resourceClass).toLowerCase() === "steam"))
            return;
        // Steam maps this utility above the editor. Hide it in the compositor
        // before the first paint, even if Steam is still processing the close.
        window.opacity = 0;
        window.keepAbove = false;
        window.keepBelow = true;
        window.skipsCloseAnimation = true;
        // Let Steam handle steam://close/keyboard and restore its input context.
    }
    window.captionChanged.connect(suppressKeyboard);
    suppressKeyboard();
}

workspace.windowAdded.connect(watch);
workspace.windowList().forEach(watch);
