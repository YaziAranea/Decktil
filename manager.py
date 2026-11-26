#!/usr/bin/env python3
"""Guided setup and keyboard selection for Steam Deck."""
import json
import os
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
import shutil
import sys

try:
    import gi
    gi.require_version("Gtk", "3.0")
    from gi.repository import Gio, GLib, Gtk, Pango
except (ImportError, ValueError):
    locale = os.environ.get("LANGUAGE") or os.environ.get("LC_ALL") or os.environ.get("LANG", "")
    message = ("Для мастера нужны GTK 3 и PyGObject для системного Python. Откройте мастер в настольном режиме Steam Deck."
               if locale.startswith("ru") else
               "This manager needs GTK 3 and PyGObject for the system Python. Open it in Steam Deck Desktop Mode.")
    if __name__ == "__main__" and shutil.which("kdialog"):
        os.execlp("kdialog", "kdialog", "--error", message)
    raise SystemExit(message)


SETUP = Path(__file__).resolve().with_name("setup.py")
LANGUAGE_FILE = Path(GLib.get_user_config_dir()) / "deck-plasma-keyboard" / "manager-language"
preference_writes = ThreadPoolExecutor(max_workers=1, thread_name_prefix="keyboard-preferences")


def preferred_language():
    try:
        code = LANGUAGE_FILE.read_text(encoding="ascii").strip()
    except (OSError, UnicodeError):
        code = ""
    if code in ("ru", "en"):
        return 0 if code == "ru" else 1
    return 0 if GLib.get_language_names()[0].lower().startswith("ru") else 1


def save_language(code):
    try:
        LANGUAGE_FILE.parent.mkdir(parents=True, exist_ok=True)
        GLib.file_set_contents(str(LANGUAGE_FILE), (code + "\n").encode("ascii"))
    except (OSError, GLib.Error):
        pass  # The selection still applies to this window on a read-only home.


COPY = {
    "title": ("Decktil", "Decktil"),
    "language": ("Язык интерфейса", "Interface language"),
    "heading": ("Экранная клавиатура", "On-screen keyboard"),
    "checking": ("Проверяем, какая клавиатура включена…", "Checking the active keyboard…"),
    "current_plasma": ("Decktil включён во всех трёх режимах.", "Decktil is enabled in all three modes."),
    "current_steam": ("Во всех трёх режимах выбрана клавиатура Steam.", "The Steam keyboard is selected in all three modes."),
    "current_mixed": ("Режимы настроены по-разному. Выберите клавиатуру для всех трёх.",
                      "The modes have different settings. Choose a keyboard for all three."),
    "desktop_mode": ("Рабочий стол KDE", "KDE Desktop"),
    "big_picture": ("Big Picture на рабочем столе", "Big Picture on the desktop"),
    "gaming_mode": ("Игровой режим SteamOS", "SteamOS Gaming Mode"),
    "provider_plasma": ("Decktil", "Decktil"),
    "provider_steam": ("Steam", "Steam"),
    "provider_foreign": ("Другая установка", "Other installation"),
    "provider_mixed": ("Нужна настройка", "Needs setup"),
    "recover": ("Предыдущее переключение прервалось. Выберите клавиатуру, чтобы завершить настройку.",
                "The last switch was interrupted. Choose a keyboard to finish setup."),
    "plasma": ("Включить Decktil", "Enable Decktil"),
    "reapply": ("Обновить Decktil", "Update Decktil"),
    "plasma_detail": ("Два трекпада и триггеры", "Two trackpads and triggers"),
    "steam": ("Вернуть Steam", "Restore Steam"),
    "steam_detail": ("Родная клавиатура Deck", "Built-in Deck keyboard"),
    "active": ("Выбрана", "Selected"),
    "setup_title": ("Первое включение Decktil", "Set up Decktil"),
    "setup_intro": ("Перейдите в настольный режим KDE Plasma на Wayland. Откройте Steam и закройте запущенные игры.",
                    "Switch to KDE Plasma Desktop Mode on Wayland. Open Steam and close any running games."),
    "prepare_info": ("Мастер добавит в библиотеку служебный ярлык и создаст для него шаблон управления. Затем вы выберете этот шаблон в Steam. Для первой установки может понадобиться интернет.",
                     "The manager will add a helper to your Steam Library and create its controller template. Next, select that template in Steam. The first installation may need an internet connection."),
    "prepare": ("Подготовить ярлык и шаблон", "Prepare shortcut and template"),
    "preparing": ("Готовим ярлык и шаблон…", "Preparing the shortcut and template…"),
    "template_title": ("Выберите шаблон в Steam", "Select the template in Steam"),
    "template_1": ("В библиотеке Steam откройте страницу «Deck Plasma Keyboard Controls» и нажмите значок контроллера.",
                   "In your Steam Library, open the Deck Plasma Keyboard Controls page and select the controller icon."),
    "template_2": ("Откройте текущую раскладку, затем «Шаблоны». Выберите «Deck Plasma Keyboard» и нажмите «Применить раскладку».",
                   "Open the current layout, then Templates. Select Deck Plasma Keyboard and choose Apply Layout."),
    "template_3": ("Вернитесь в это окно и включите Decktil.", "Return to this window and enable Decktil."),
    "verify": ("Проверить и включить Decktil", "Verify and enable Decktil"),
    "library": ("Открыть библиотеку", "Open Steam Library"),
    "offline_link": ("Шаблона нет в списке", "The template is missing"),
    "offline_title": ("Привязка при закрытом Steam", "Apply the template with Steam closed"),
    "offline_steps": ("В Steam выберите «Steam › Выход». Когда Steam полностью закроется, нажмите «Привязать шаблон». Подойдёт для одной учётной записи и ярлыка без своей раскладки.",
                      "Choose Steam › Exit. Once Steam has fully quit, select Apply template. This works for one account and a helper with no existing layout."),
    "bind": ("Привязать шаблон", "Apply template"),
    "binding": ("Привязываем шаблон…", "Applying the template…"),
    "launch_title": ("Снова откройте Steam", "Start Steam again"),
    "launch_steps": ("Когда загрузится библиотека, вернитесь в мастер. Он проверит шаблон и включит Decktil.",
                     "Once the Library has loaded, return here. The manager will verify the template and enable Decktil."),
    "launch": ("Открыть Steam", "Open Steam"),
    "shortcut_missing": ("Не видите ярлык? Полностью закройте Steam и запустите его снова. При необходимости добавьте в библиотеку стороннюю игру, выбрав файл:",
                         "Shortcut missing? Fully quit and reopen Steam. If needed, add a non-Steam game to your Library and choose this file:"),
    "shortcut_help": ("Ярлыка нет в библиотеке", "The shortcut is missing"),
    "back": ("Назад", "Back"),
    "refresh": ("Проверить снова", "Check again"),
    "personal": ("Личный словарь", "Personal dictionary"),
    "appearance": ("Оформление", "Appearance"),
    "appearance_detail": ("Темы и цвета клавиш", "Themes and key colors"),
    "chrome": ("Автооткрытие в Chrome", "Reopen in Chrome"),
    "chrome_off": ("Выключено", "Off"),
    "chrome_on": ("Включено", "On"),
    "chrome_mixed": ("Частично настроено", "Partially configured"),
    "chrome_unavailable": ("Chrome Flatpak недоступен", "Chrome Flatpak unavailable"),
    "chrome_help": ("На рабочем столе нажмите B, чтобы скрыть Decktil и оставить введённый адрес. Выберите подсказку Chrome мышью или верните указатель в активную адресную строку, чтобы снова открыть клавиатуру.",
                    "On the desktop, press B to hide Decktil and keep the address you typed. Select a Chrome suggestion with the pointer, or move it back into the active address bar to reopen the keyboard."),
    "chrome_settings": ("Для автооткрытия Google Chrome из Discover (Flatpak) должен сообщать службе доступности положение поля ввода.",
                        "To reopen Decktil, Google Chrome from Discover (Flatpak) needs to expose the position of the input field through accessibility services."),
    "chrome_own": ("Эти параметры уже заданы вне Decktil.", "These settings were already configured outside Decktil."),
    "chrome_restart": ("После изменения полностью выйдите из Chrome через его меню и снова откройте браузер.",
                       "After changing these settings, quit Chrome from its menu and reopen it."),
    "chrome_enable": ("Включить для Chrome", "Enable for Chrome"),
    "chrome_restore": ("Вернуть прежние настройки", "Restore previous settings"),
    "chrome_conflict": ("В файле chrome-flags.conf выбран другой режим доступности Chrome. Проверьте параметр renderer-accessibility и повторите.",
                        "Chrome has a different accessibility mode in chrome-flags.conf. Review its renderer-accessibility flag and try again."),
    "chrome_saving": ("Сохраняем настройки Chrome…", "Saving Chrome settings…"),
    "learning_on": ("Обучение включено", "Learning on"),
    "learning_off": ("Обучение выключено", "Learning off"),
    "learn": ("Запоминать мои слова и выражения", "Learn my words and phrases"),
    "learn_details": ("Частые слова и сочетания из двух или трёх слов поднимаются в подсказках. Словарь хранится на этом устройстве.",
                      "Words and two- or three-word phrases you use often move up in suggestions. Your dictionary stays on this device."),
    "personal_saving": ("Сохраняем настройки словаря…", "Saving dictionary settings…"),
    "clear_personal": ("Очистить личный словарь", "Clear personal dictionary"),
    "confirm_clear": ("Удалить все выученные слова и сочетания на обоих языках? Восстановить их после очистки нельзя.",
                      "Delete all learned words and phrases in both languages? This cannot be undone."),
    "cancel": ("Отмена", "Cancel"),
    "clear": ("Очистить", "Clear"),
    "switch_plasma": ("Настраиваем Decktil для трёх режимов…", "Setting up Decktil for all three modes…"),
    "switch_steam": ("Возвращаем клавиатуру Steam…", "Restoring Steam Keyboard…"),
    "ready_plasma": ("Steam + X открывает и скрывает Decktil. В текстовом поле рабочего стола также работает X. На рабочем столе B скрывает панель и возвращает мышь, сохраняя введённый адрес. X стирает символ, удержание повторяет удаление.",
                     "Steam + X opens and hides Decktil. In a desktop text field, X also opens it. On the desktop, B hides the panel and restores the mouse while keeping the address you typed. X deletes one character. Hold X to keep deleting."),
    "ready_steam": ("Нажмите Steam + X, чтобы открыть штатную клавиатуру.", "Press Steam + X to open the built-in keyboard."),
    "retry": ("Повторить", "Try again"),
    "details": ("Технические сведения", "Technical details"),
    "unmanaged": ("Есть файлы другой установки. Сохраните её отдельно и освободите конфликтующие пути перед настройкой.",
                  "Files from another installation are present. Back it up and resolve the conflicting paths before setup."),
    "failed": ("Не удалось выполнить действие. Откройте технические сведения, исправьте указанную причину и повторите.",
               "The action could not finish. Open the technical details, address the reported problem and try again."),
    "no_status": ("Не удалось определить текущую клавиатуру.", "Could not determine the active keyboard."),
    "desktop": ("Откройте мастер от обычного пользователя в настольном сеансе KDE Plasma на Wayland.",
                "Open the manager as your regular user in a KDE Plasma Wayland desktop session."),
    "missing_dep": ("Не хватает системного компонента. Проверьте его имя в технических сведениях. Нужна SteamOS с установленной Plasma Keyboard.",
                    "A system component is missing. Check its name in the technical details. SteamOS with Plasma Keyboard installed is required."),
    "game_open": ("Закройте игру, вернитесь на рабочий стол и повторите.", "Close the game, return to Desktop Mode and try again."),
    "steam_closed": ("Запустите Steam в настольном режиме. Когда библиотека загрузится, повторите.",
                     "Open Steam in Desktop Mode. Wait for the Library to load and try again."),
    "need_exit": ("Выберите «Steam › Выход», дождитесь полного закрытия Steam и повторите.",
                  "Choose Steam › Exit, wait for Steam to fully quit and try again."),
    "need_shortcut": ("Откройте библиотеку и проверьте, есть ли «Deck Plasma Keyboard Controls». Если ярлыка нет, используйте подсказку под шагами мастера.",
                      "Look for Deck Plasma Keyboard Controls in your Steam Library. If it is missing, open the shortcut help below the setup steps."),
    "need_layout": ("В настройках контроллера Steam сохраните настольную раскладку встроенного контроллера как личную. Затем повторите подготовку.",
                    "In Steam controller settings, save the built-in controller’s desktop layout as a personal layout. Then retry preparation."),
    "need_template": ("Проверьте раскладку служебного ярлыка в Steam. После выбора шаблона нажмите «Применить раскладку» и вернитесь в мастер.",
                      "Check the helper’s controller layout in Steam. After selecting the template, choose Apply Layout and return here."),
    "picker_only": ("Откройте Steam в нужной учётной записи и выберите шаблон через настройки контроллера служебного ярлыка. Нажмите «Назад», чтобы увидеть шаги.",
                    "Open Steam in the account you want to use and select the template in the helper’s controller settings. Choose Back to see the steps."),
    "permission": ("Проверьте доступ пользователя к указанному файлу или устройству в технических сведениях.",
                   "Check your user’s access to the file or device shown in the technical details."),
    "open_failed": ("Откройте Steam через меню приложений рабочего стола.", "Open Steam from the desktop application menu."),
}
CSS = b"""
window.deck-manager { background-color: @theme_bg_color; }
.deck-heading { font-size: 26px; font-weight: bold; }
.deck-subheading { font-size: 18px; font-weight: bold; }
.deck-caption { opacity: .78; }
.deck-status { font-size: 15px; }
button.deck-mode { padding: 13px 15px; min-height: 62px; border-radius: 13px; }
button.deck-mode.deck-current { border-color: @theme_selected_bg_color; box-shadow: inset 0 0 0 1px @theme_selected_bg_color; }
.deck-settings { border: 1px solid alpha(@theme_fg_color,.21); border-radius: 12px; }
button.deck-setting { padding: 4px 14px; min-height: 38px; border-radius: 10px; }
.deck-setting-title { font-weight: bold; }
.deck-card { padding: 16px; border: 1px solid alpha(@theme_fg_color,.22); border-radius: 8px; }
.deck-action { min-height: 38px; padding-left: 12px; padding-right: 12px; }
.deck-link { min-height: 34px; }
.deck-error { border-left: 3px solid @warning_color; padding-left: 12px; }
"""


def friendly_error(detail):
    text = detail.lower()
    for key, fragments in (
        ("chrome_conflict", ("conflicting renderer accessibility flag",)),
        ("chrome_unavailable", ("google chrome flatpak is not installed",)),
        ("picker_only", ("single registered helper", "existing binding", "use the steam template picker")),
        ("need_exit", ("choose steam > exit", "steam exits")),
        ("desktop", ("wayland session", "desktop user", "plasma wayland")),
        ("missing_dep", ("missing prerequisite", "no module named", "filesystem ru/en", "no such file or directory")),
        ("game_open", ("focus the plasma desktop", "not using its desktop configuration", "running game")),
        ("steam_closed", ("steam is not running",)),
        ("need_layout", ("save a personal",)),
        ("need_shortcut", ("has not saved", "multiple steam helpers")),
        ("need_template", ("deck keyboard configuration", "template picker", "selecting the template")),
        ("unmanaged", ("unmanaged", "modified file")),
        ("permission", ("permission denied",)),
    ):
        if any(fragment in text for fragment in fragments):
            return key
    return "failed"


def session_keyboards(state):
    providers = {"enabled": "plasma", "disabled": "steam", "missing": "steam",
                 "foreign": "foreign", "mixed": "mixed"}
    return {"desktop_mode": state["mode"],
            "big_picture": providers[state["big_picture"]],
            "gaming_mode": providers[state["gaming_mode"]]}


def label(text, style=None):
    widget = Gtk.Label(label=text, xalign=0)
    widget.set_line_wrap(True)
    widget.set_line_wrap_mode(Pango.WrapMode.WORD_CHAR)
    if style:
        widget.get_style_context().add_class(style)
    return widget


class KeyboardWindow(Gtk.ApplicationWindow):
    def __init__(self, application):
        super().__init__(application=application)
        self.lang = preferred_language()
        self.state = None
        self.page = "choose"
        self.appearance_draft = {}
        self.appearance_custom = {}
        self.error = None
        self.pending = None
        self.busy_text = ""
        display = self.get_display()
        monitor = display.get_primary_monitor() or display.get_monitor(0)
        area = monitor.get_workarea() if monitor else None
        width = min(730, area.width - 40) if area and area.width >= 360 else 730
        self.wizard_height = min(615, area.height - 100) if area and area.height >= 370 else 615
        self.set_default_size(width, min(480, self.wizard_height))
        self.height_before_setup = None
        self.showing_setup = False
        self.set_icon_name("input-keyboard")
        self.get_style_context().add_class("deck-manager")
        provider = Gtk.CssProvider()
        provider.load_from_data(CSS)
        Gtk.StyleContext.add_provider_for_screen(self.get_screen(), provider,
                                                Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION)
        self.header = Gtk.HeaderBar(show_close_button=True)
        self.set_titlebar(self.header)
        self.language = Gtk.ComboBoxText()
        self.language.append_text("Русский")
        self.language.append_text("English")
        self.language.set_active(self.lang)
        self.language.connect("changed", self.change_language)
        self.header.pack_end(self.language)
        scroll = Gtk.ScrolledWindow()
        scroll.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
        self.add(scroll)
        self.body = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=14)
        self.body.set_border_width(22)
        scroll.add(self.body)
        self.connect("delete-event", self.closing)
        self.render()
        self.show_all()
        self.refresh()

    def t(self, key):
        return COPY[key][self.lang]

    def change_language(self, combo):
        self.lang = combo.get_active()
        preference_writes.submit(save_language, "ru" if self.lang == 0 else "en")
        self.render()

    def closing(self, *_):
        if self.pending:
            # Let an in-progress setup transaction finish if its window is closed.
            self.hide()
            return True
        return False

    def navigate(self, page):
        if page == "appearance" and self.page != page:
            from src import appearance
            self.appearance_draft = appearance.load()
            self.appearance_custom = appearance.load_custom() or {}
        self.page = page
        self.error = None
        self.render()

    def button(self, key, callback, primary=False, flat=False):
        button = Gtk.Button.new_with_label(self.t(key))
        button.get_style_context().add_class("deck-link" if flat else "deck-action")
        if flat:
            button.set_relief(Gtk.ReliefStyle.NONE)
        if primary:
            button.get_style_context().add_class("suggested-action")
        button.connect("clicked", lambda *_: callback())
        return button

    def mode_button(self, mode, selected, enabled):
        button = Gtk.Button()
        style = button.get_style_context()
        style.add_class("deck-mode")
        if selected:
            style.add_class("deck-current")
        content = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4)
        top = Gtk.Box(spacing=8)
        icon = "object-select-symbolic" if selected else "input-keyboard-symbolic"
        top.pack_start(Gtk.Image.new_from_icon_name(icon, Gtk.IconSize.BUTTON), False, False, 0)
        title = self.t("reapply" if selected and mode == "plasma" else mode)
        top.pack_start(label(title, "deck-subheading"), True, True, 0)
        content.add(top)
        content.add(label(self.t(mode + "_detail"), "deck-caption"))
        button.add(content)
        name = title + (" · " + self.t("active") if selected else "")
        button.get_accessible().set_name(name)
        button.set_sensitive(enabled)
        button.connect("clicked", lambda *_: self.select(mode))
        return button

    def card(self, title):
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=12)
        box.get_style_context().add_class("deck-card")
        box.pack_start(label(self.t(title), "deck-subheading"), False, False, 0)
        self.body.pack_start(box, False, False, 0)
        return box

    def settings_row(self, key, detail, icon):
        row = Gtk.Button()
        row.set_relief(Gtk.ReliefStyle.NONE)
        row.get_style_context().add_class("deck-setting")
        content = Gtk.Box(spacing=12)
        content.pack_start(Gtk.Image.new_from_icon_name(icon, Gtk.IconSize.BUTTON), False, False, 0)
        content.pack_start(label(self.t(key), "deck-setting-title"), True, True, 0)
        content.pack_start(label(self.t(detail), "deck-caption"), False, False, 0)
        content.pack_end(Gtk.Image.new_from_icon_name("go-next-symbolic", Gtk.IconSize.MENU), False, False, 0)
        row.add(content)
        row.get_accessible().set_name(self.t(key))
        row.get_accessible().set_description(self.t(detail))
        row.connect("clicked", lambda *_: self.navigate(key))
        return row

    def actions(self, box, *items):
        row = Gtk.FlowBox()
        row.set_halign(Gtk.Align.FILL)
        row.set_selection_mode(Gtk.SelectionMode.NONE)
        row.set_min_children_per_line(1)
        row.set_max_children_per_line(len(items))
        row.set_column_spacing(4)
        for item in items:
            row.add(item)
        box.pack_start(row, False, False, 0)

    def select(self, mode):
        if mode == "plasma" and not self.state["setup_complete"]:
            self.navigate("template" if self.state["prepared"] else "prepare")
        else:
            self.switch(mode)

    def switch(self, mode):
        self.perform(["mode", mode], "switch_" + mode, "choose")

    def render(self):
        self.header.set_title(self.t("title"))
        self.set_title(self.t("title"))
        self.language.get_accessible().set_name(self.t("language"))
        for child in self.body.get_children():
            child.destroy()
        showing_setup = self.page != "choose"
        if showing_setup != self.showing_setup and not self.is_maximized():
            width, height = self.get_size()
            if showing_setup and height < self.wizard_height:
                self.height_before_setup = height
                self.resize(width, self.wizard_height)
            elif not showing_setup and self.height_before_setup is not None:
                if abs(height - self.wizard_height) < 8:
                    self.resize(width, self.height_before_setup)
                self.height_before_setup = None
        self.showing_setup = showing_setup
        if self.page == "appearance":
            from appearance_page import AppearancePage
            self.body.add(AppearancePage(self.lang, self.appearance_draft, self.appearance_custom,
                                         lambda: self.navigate("choose")))
            self.body.show_all()
            return
        heading = Gtk.Box(spacing=8)
        heading.pack_start(label(self.t("heading"), "deck-heading"), True, True, 0)
        if not self.pending and (self.page == "choose" or not self.state):
            refresh = Gtk.Button.new_from_icon_name("view-refresh-symbolic", Gtk.IconSize.BUTTON)
            refresh.set_relief(Gtk.ReliefStyle.NONE)
            refresh.set_tooltip_text(self.t("refresh"))
            refresh.get_accessible().set_name(self.t("refresh"))
            refresh.connect("clicked", lambda *_: self.refresh())
            heading.pack_end(refresh, False, False, 0)
        self.body.pack_start(heading, False, False, 0)
        keyboards = session_keyboards(self.state) if self.state else {}
        current = keyboards.get("desktop_mode")
        if any(provider != current for provider in keyboards.values()):
            current = "mixed"
        supported = self.state is not None and self.state.get("supported_session", True)
        status = self.busy_text if self.pending else ("current_" + current if current else "no_status")
        if self.state and not self.pending:
            if not supported:
                status = "desktop"
            elif self.state.get("recovery_pending"):
                status = "recover"
        status_row = Gtk.Box(spacing=10)
        if self.pending:
            spinner = Gtk.Spinner()
            spinner.start()
            status_row.pack_start(spinner, False, False, 0)
        status_row.pack_start(label(self.t(status), "deck-status"), True, True, 0)
        self.body.pack_start(status_row, False, False, 0)

        modes = Gtk.Grid(column_spacing=10, column_homogeneous=True)
        for col, mode in enumerate(("plasma", "steam")):
            enabled = supported and not self.pending and self.state["installation"] != "unmanaged"
            if enabled and mode == "steam":
                enabled = current != "steam" or self.state.get("recovery_pending", False)
            if enabled and mode == "plasma":
                enabled = self.state["can_switch"] or not self.state["setup_complete"]
            modes.attach(self.mode_button(mode, current == mode, enabled), col, 0, 1, 1)
        self.body.pack_start(modes, False, False, 0)

        if self.page == "choose" and keyboards and supported:
            summary = Gtk.Grid(row_spacing=4, column_spacing=16)
            for row, (session, provider) in enumerate(keyboards.items()):
                summary.attach(label(self.t(session), "deck-caption"), 0, row, 1, 1)
                summary.attach(label(self.t("provider_" + provider)), 1, row, 1, 1)
            self.body.pack_start(summary, False, False, 0)

        if self.error:
            err = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=5)
            err.get_style_context().add_class("deck-error")
            err.pack_start(label(self.t(self.error[0])), False, False, 0)
            details = Gtk.Expander(label=self.t("details"))
            message = label(self.error[1])
            message.set_selectable(True)
            message.set_max_width_chars(65)
            details.add(message)
            err.add(details)
            self.body.add(err)

        if self.page in ("personal", "clear_personal"):
            self.personal_page()
        elif self.page == "chrome":
            self.chrome_page()
        elif showing_setup:
            self.setup_page()
        elif self.state and self.state["installation"] == "unmanaged":
            self.body.add(label(self.t("unmanaged")))
        elif supported and current in ("steam", "plasma"):
            self.body.add(label(self.t("ready_" + current), "deck-caption"))
        if not self.pending and self.state and self.page == "choose":
            settings = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
            settings.get_style_context().add_class("deck-settings")
            settings.add(self.settings_row("appearance", "appearance_detail", "applications-graphics-symbolic"))
            settings.add(Gtk.Separator(orientation=Gtk.Orientation.HORIZONTAL))
            learning = "learning_on" if self.state.get("personal_enabled") else "learning_off"
            settings.add(self.settings_row("personal", learning, "accessories-dictionary-symbolic"))
            chrome = self.state["chrome_accessibility"]
            if chrome != "unavailable" or self.state["chrome_managed"]:
                settings.add(Gtk.Separator(orientation=Gtk.Orientation.HORIZONTAL))
                settings.add(self.settings_row("chrome", "chrome_" + chrome, "web-browser-symbolic"))
            self.body.pack_start(settings, False, False, 0)
        self.body.show_all()

    def chrome_page(self):
        box = self.card("chrome")
        box.set_sensitive(not self.pending and self.state is not None)
        if self.state is None:
            return
        current = self.state["chrome_accessibility"]
        managed = self.state["chrome_managed"]
        box.add(label(self.t("chrome_help")))
        box.add(label(self.t("chrome_settings"), "deck-caption"))
        box.add(label(self.t("chrome_" + current), "deck-setting-title"))
        if current in ("on", "mixed") and not managed:
            box.add(label(self.t("chrome_own"), "deck-caption"))
        actions = []
        if current in ("off", "mixed"):
            actions.append(self.button("chrome_enable", lambda: self.perform(
                ["chrome", "on"], "chrome_saving", "chrome"), True))
        if managed:
            actions.append(self.button("chrome_restore", lambda: self.perform(
                ["chrome", "off"], "chrome_saving", "chrome")))
        box.add(label(self.t("chrome_restart"), "deck-caption"))
        self.actions(box, *actions, self.button("back", lambda: self.navigate("choose"), flat=True))

    def personal_page(self):
        box = self.card("personal")
        box.set_sensitive(self.pending is None and self.state is not None)
        if self.page == "clear_personal":
            box.add(label(self.t("confirm_clear")))
            self.actions(box, self.button("cancel", lambda: self.navigate("personal")),
                         self.button("clear", lambda: self.perform(["personal", "clear"], "personal_saving", "personal")))
            return
        learn = Gtk.CheckButton.new_with_label(self.t("learn"))
        learn.set_active(bool(self.state and self.state.get("personal_enabled")))
        learn.connect("toggled", lambda button: self.perform(
            ["personal", "on" if button.get_active() else "off"], "personal_saving", "personal"))
        box.add(learn)
        box.add(label(self.t("learn_details"), "deck-caption"))
        self.actions(box, self.button("back", lambda: self.navigate("choose")),
                     self.button("clear_personal", lambda: self.navigate("clear_personal"), flat=True))

    def setup_page(self):
        title = {"prepare": "setup_title", "template": "template_title",
                 "offline": "offline_title", "launch": "launch_title"}[self.page]
        box = self.card(title)
        box.set_sensitive(not self.pending)
        if self.page == "prepare":
            for key in ("setup_intro", "prepare_info"):
                box.add(label(self.t(key)))
            self.actions(box, self.button("prepare", lambda: self.perform(["prepare"], "preparing", "template"), True),
                         self.button("back", lambda: self.navigate("choose"), flat=True))
        elif self.page == "template":
            for number, key in enumerate(("template_1", "template_2", "template_3"), 1):
                box.add(label(f"{number}.  {self.t(key)}"))
            self.actions(box, self.button("verify", lambda: self.switch("plasma"), True),
                         self.button("library", self.open_steam))
            self.actions(box, self.button("offline_link", lambda: self.navigate("offline"), flat=True),
                         self.button("back", lambda: self.navigate("choose"), flat=True))
            explanation = Gtk.Expander(label=self.t("shortcut_help"))
            data_home = Path(os.environ.get("XDG_DATA_HOME") or Path.home() / ".local/share")
            path = data_home / "deck-plasma-keyboard" / "Deck Plasma Keyboard Controls"
            info = label(self.t("shortcut_missing") + "\n" + str(path))
            info.set_selectable(True)
            explanation.add(info)
            box.add(explanation)
        elif self.page == "offline":
            box.add(label(self.t("offline_steps")))
            self.actions(box, self.button("bind", lambda: self.perform(["bind"], "binding", "launch"), True),
                         self.button("back", lambda: self.navigate("template"), flat=True))
        else:
            box.add(label(self.t("launch_steps")))
            self.actions(box, self.button("launch", self.open_steam),
                         self.button("verify", lambda: self.switch("plasma"), True),
                         self.button("back", lambda: self.navigate("template"), flat=True))

    def open_steam(self):
        def finished(_, result):
            try:
                Gio.AppInfo.launch_default_for_uri_finish(result)
            except GLib.Error as exc:
                self.error = ("open_failed", str(exc))
                self.render()
        Gio.AppInfo.launch_default_for_uri_async("steam://open/games", None, None, finished)

    def run(self, args, busy_text, callback):
        if self.pending:
            return
        self.busy_text = busy_text
        try:
            process = Gio.Subprocess.new([sys.executable, str(SETUP), *args],
                                         Gio.SubprocessFlags.STDOUT_PIPE | Gio.SubprocessFlags.STDERR_PIPE)
        except GLib.Error as exc:
            callback(False, "", str(exc))
            return
        self.pending = process
        self.render()

        def finished(proc, result):
            try:
                _, output, detail = proc.communicate_utf8_finish(result)
                ok = proc.get_successful()
            except GLib.Error as exc:
                ok, output, detail = False, "", str(exc)
            self.pending = None
            if not self.get_visible():
                self.destroy()
                return
            callback(ok, output or "", detail or "")
        process.communicate_utf8_async(None, None, finished)

    def refresh(self, previous_error=None):
        self.run(["status"], "checking", lambda ok, output, detail:
                 self.read_status(ok, output, detail, previous_error))

    def read_status(self, ok, output, detail, previous_error):
        try:
            if not ok:
                raise ValueError(detail or output)
            state = json.loads(output)
            if state["mode"] not in ("steam", "plasma", "mixed") or state["installation"] not in (
                    "managed", "manual", "missing", "unmanaged"):
                raise ValueError("Unexpected setup status")
            for key in ("prepared", "can_switch", "setup_complete", "chrome_managed"):
                if not isinstance(state[key], bool):
                    raise ValueError("Missing setup status: " + key)
            if state["chrome_accessibility"] not in ("unavailable", "off", "on", "mixed"):
                raise ValueError("Unexpected Chrome setup status")
            session_keyboards(state)  # Reject unknown or missing service states before rendering.
            self.state = state
            self.error = previous_error
        except (ValueError, KeyError, TypeError) as exc:
            self.state = None
            self.error = (friendly_error(str(exc)), str(exc)[-3000:])
        self.render()

    def perform(self, args, progress, next_page):
        self.error = None

        def finished(ok, output, detail):
            if not ok:
                message = (detail or output).strip() or "setup.py exited with an error"
                self.refresh((friendly_error(message), message[-3000:]))
                return
            self.page = next_page
            self.refresh()
        self.run(args, progress, finished)


def main():
    app_id = "io.github.YaziAranea.SteamDeckPlasmaKeyboard"
    version = SETUP.parent.name
    if len(version) == 64 and all(char in "0123456789abcdef" for char in version):
        # A manager still open from an older download must not receive this launch.
        app_id += ".v" + version
    app = Gtk.Application(application_id=app_id)
    def activate(application):
        window = application.get_active_window()
        if window is None:
            window = KeyboardWindow(application)
        window.present()
    app.connect("activate", activate)
    return app.run(sys.argv)


if __name__ == "__main__":
    sys.exit(main())
