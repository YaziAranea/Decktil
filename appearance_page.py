"""Edit Decktil's local keyboard palette in the GTK manager."""
import math

import cairo
import gi
gi.require_version("Gtk", "3.0")
gi.require_version("PangoCairo", "1.0")
from gi.repository import Gdk, Gtk, Pango, PangoCairo

from src import appearance


COPY = {
    "title": ("Оформление клавиатуры", "Keyboard appearance"),
    "preset": ("Тема", "Theme"),
    "default": ("Системная", "System"),
    "midnight": ("Полночь", "Midnight"),
    "lagoon": ("Лагуна", "Lagoon"),
    "ember": ("Угли", "Ember"),
    "porcelain": ("Фарфор", "Porcelain"),
    "custom": ("Своя", "Custom"),
    "customise": ("Настроить цвета и градиент", "Customize colors and gradient"),
    "direction": ("Направление", "Direction"),
    "vertical": ("Сверху вниз", "Top to bottom"),
    "horizontal": ("Слева направо", "Left to right"),
    "backgroundStart": ("Начало фона", "Background start"),
    "backgroundEnd": ("Конец фона", "Background end"),
    "keyStart": ("Начало клавиш", "Key start"),
    "keyEnd": ("Конец клавиш", "Key end"),
    "text": ("Надписи", "Key labels"),
    "accent": ("Акцент", "Accent"),
    "contrast": ("Подобрать цвет надписей", "Find a readable label color"),
    "preview": ("Предпросмотр клавиатуры", "Keyboard preview"),
    "apply": ("Применить", "Apply"),
    "saved": ("Сохранено", "Saved"),
    "save_error": ("Не удалось сохранить тему", "Could not save the theme"),
    "back": ("Назад", "Back"),
}

COLORS = appearance.COLORS
TILE_CSS = b"""
button.deck-theme { padding: 5px; border-radius: 10px; border: 1px solid alpha(@theme_fg_color, .2);
                   background-image: none; background-color: transparent; box-shadow: none; }
button.deck-theme:hover { background-color: alpha(@theme_fg_color, .07); }
button.deck-theme:checked { border-color: @theme_selected_bg_color; color: @theme_fg_color;
                            background-color: alpha(@theme_selected_bg_color, .12); }
"""


def rgba(value):
    colour = Gdk.RGBA()
    if not colour.parse(value):
        raise ValueError(value)
    return colour


def html_colour(colour):
    return "#" + "".join(f"{round(255 * channel):02X}" for channel in (colour.red, colour.green, colour.blue))


def rounded_path(cr, x, y, w, h, radius):
    radius = min(radius, w / 2, h / 2)
    cr.new_sub_path()
    for cx, cy, angle in ((x+w-radius, y+radius, -90), (x+w-radius, y+h-radius, 0),
                          (x+radius, y+h-radius, 90), (x+radius, y+radius, 180)):
        cr.arc(cx, cy, radius, math.radians(angle), math.radians(angle+90))
    cr.close_path()


def paint_gradient(cr, palette, part, x, y, w, h, radius):
    horizontal = palette["direction"] == "horizontal"
    gradient = cairo.LinearGradient(x, y, x+w if horizontal else x, y if horizontal else y+h)
    for stop, suffix in ((0, "Start"), (1, "End")):
        shade = rgba(palette[part + suffix])
        gradient.add_color_stop_rgb(stop, shade.red, shade.green, shade.blue)
    rounded_path(cr, x, y, w, h, radius)
    cr.set_source(gradient)
    cr.fill()


class AppearancePage(Gtk.Box):
    def __init__(self, lang, draft, custom, back):
        super().__init__(orientation=Gtk.Orientation.VERTICAL, spacing=14)
        self.lang = lang
        self.draft = draft
        self.custom = custom
        self.saved = appearance.load()
        self.updating = False
        heading = Gtk.Box(spacing=12)
        previous = Gtk.Button.new_from_icon_name("go-previous-symbolic", Gtk.IconSize.BUTTON)
        previous.set_tooltip_text(self.t("back"))
        previous.get_accessible().set_name(self.t("back"))
        previous.connect("clicked", lambda *_: back())
        heading.pack_start(previous, False, False, 0)
        title = Gtk.Label(label=self.t("title"), xalign=0)
        title.set_line_wrap(True)
        title.get_style_context().add_class("deck-heading")
        heading.pack_start(title, True, True, 0)
        self.apply = Gtk.Button.new_with_label(self.t("apply"))
        self.apply.get_style_context().add_class("suggested-action")
        self.apply.get_style_context().add_class("deck-action")
        self.apply.connect("clicked", self.commit)
        heading.pack_end(self.apply, False, False, 0)
        self.add(heading)
        self.preview = Gtk.DrawingArea()
        self.preview.set_size_request(-1, 164)
        self.preview.get_accessible().set_name(self.t("preview"))
        self.preview.connect("draw", self.draw_preview)
        self.add(self.preview)

        themes = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6)
        label = Gtk.Label(label=self.t("preset"), xalign=0)
        label.get_style_context().add_class("deck-subheading")
        themes.add(label)
        gallery = Gtk.FlowBox(homogeneous=True, selection_mode=Gtk.SelectionMode.NONE,
                              min_children_per_line=2, max_children_per_line=6, column_spacing=2, row_spacing=2)
        gallery.get_accessible().set_name(self.t("preset"))
        themes.add(gallery)
        self.add(themes)
        self.theme_buttons = {}
        group = None
        style = Gtk.CssProvider()
        style.load_from_data(TILE_CSS)
        for name in ("default", "midnight", "lagoon", "ember", "porcelain", "custom"):
            button = Gtk.RadioButton.new_from_widget(group)
            if group is None:
                group = button
            button.set_mode(False)
            button.get_style_context().add_class("deck-theme")
            button.get_style_context().add_provider(style, Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION)
            button.get_accessible().set_name(self.t(name))
            content = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=3)
            swatch = Gtk.DrawingArea()
            swatch.set_size_request(62, 31)
            swatch.connect("draw", self.draw_swatch, name, button)
            content.add(swatch)
            content.add(Gtk.Label(label=self.t(name)))
            button.add(content)
            button.connect("toggled", self.choose_theme, name)
            gallery.add(button)
            button.get_parent().set_can_focus(False)
            self.theme_buttons[name] = button

        self.details = Gtk.Expander(label=self.t("customise"), spacing=8)
        self.details.set_expanded(self.draft["preset"] == "custom")
        self.add(self.details)
        grid = Gtk.Grid(row_spacing=7, column_spacing=18, column_homogeneous=True)
        self.details.add(grid)
        self.pickers = {}
        for i, key in enumerate(COLORS):
            picker = Gtk.ColorButton()
            picker.set_title(self.t(key))
            picker.set_use_alpha(False)
            self.field(grid, key, picker, i % 2, i // 2)
            picker.connect("color-set", self.pick, key)
            self.pickers[key] = picker
        self.direction = Gtk.ComboBoxText()
        for choice in appearance.DIRECTIONS:
            self.direction.append(choice, self.t(choice))
        self.field(grid, "direction", self.direction, 0, 3)
        self.direction.connect("changed", self.choose_direction)
        self.contrast = Gtk.Button.new_with_label(self.t("contrast"))
        self.contrast.connect("clicked", lambda *_: self.edit("text", appearance.readable_ink(self.preview_palette())))
        grid.attach(self.contrast, 1, 3, 1, 1)
        self.message = Gtk.Label(xalign=0)
        self.message.set_line_wrap(True)
        self.message.set_no_show_all(True)
        self.add(self.message)
        self.sync()

    def t(self, key):
        return COPY[key][self.lang]

    def field(self, grid, key, widget, col, row):
        pair = Gtk.Box(spacing=8)
        label = Gtk.Label(label=self.t(key), xalign=0)
        label.set_line_wrap(True)
        label.set_mnemonic_widget(widget)
        widget.get_accessible().set_name(self.t(key))
        widget.set_size_request(60, 32)
        pair.pack_start(label, False, False, 0)
        pair.pack_end(widget, isinstance(widget, Gtk.ComboBox), True, 0)
        grid.attach(pair, col, row, 1, 1)

    def choose_theme(self, button, preset):
        if self.updating or not button.get_active():
            return
        values = (self.custom or self.preview_palette()) if preset == "custom" else appearance.PRESETS[preset]
        self.draft.update(values, preset=preset)
        self.details.set_expanded(preset == "custom")
        self.sync()

    def edit(self, key, value):
        if self.draft["preset"] == "default":
            self.draft.update(self.preview_palette())
        self.draft.update({key: value, "preset": "custom"})
        self.custom.update(self.draft)
        self.sync()

    def choose_direction(self, combo):
        if not self.updating:
            self.edit("direction", combo.get_active_id())

    def pick(self, picker, key):
        self.edit(key, html_colour(picker.get_rgba()))

    def sync(self):
        self.updating = True
        try:
            self.theme_buttons[self.draft["preset"]].set_active(True)
            self.direction.set_active_id(self.draft["direction"])
            palette = self.preview_palette()
            for key, picker in self.pickers.items():
                picker.set_rgba(rgba(palette[key]))
        finally:
            self.updating = False
        self.message.hide()
        self.apply.set_label(self.t("apply"))
        self.apply.set_sensitive(self.draft != self.saved)
        self.queue_draw()

    def commit(self, *_):
        try:
            self.saved = appearance.save(self.draft)
        except (OSError, ValueError) as error:
            self.message.set_text(f"{self.t('save_error')} — {error}")
            self.message.show()
            return
        self.draft.update(self.saved)
        self.sync()
        self.apply.set_label(self.t("saved"))

    def palette_for(self, preset):
        if preset == "custom":
            return self.custom or self.preview_palette()
        palette = dict(appearance.PRESETS[preset])
        if preset == "default":
            style = self.get_style_context()
            for key, name in (("backgroundStart", "theme_bg_color"), ("backgroundEnd", "theme_bg_color"),
                              ("keyStart", "theme_base_color"), ("keyEnd", "theme_base_color"),
                              ("text", "theme_fg_color"), ("accent", "theme_selected_bg_color")):
                found, system_colour = style.lookup_color(name)
                if found:
                    palette[key] = html_colour(system_colour)
        return palette

    def preview_palette(self):
        if self.draft["preset"] == "default":
            return self.palette_for("default")
        return self.draft

    def draw_swatch(self, widget, cr, name, button):
        width, height = widget.get_allocated_width(), widget.get_allocated_height()
        palette = self.palette_for(name)
        paint_gradient(cr, palette, "background", 0, 0, width, height, 6)
        key_size = height * .5
        x = (width - 3 * key_size - 6) / 2
        for i in range(3):
            paint_gradient(cr, palette, "key", x, (height-key_size)/2, key_size, key_size, 3)
            cr.arc(x + key_size/2, height/2, 2, 0, 2*math.pi)
            Gdk.cairo_set_source_rgba(cr, rgba(palette["accent" if i == 1 else "text"]))
            cr.fill()
            x += key_size + 3
        if button.get_active():
            # A high-contrast check remains recognizable independently of the chosen colors.
            x, y = width - 9, 9
            cr.arc(x, y, 7.5, 0, 2*math.pi)
            cr.set_source_rgb(1, 1, 1)
            cr.fill_preserve()
            cr.set_source_rgba(0, 0, 0, .45)
            cr.set_line_width(1)
            cr.stroke()
            cr.move_to(x-3.5, y)
            cr.line_to(x-1, y+2.5)
            cr.line_to(x+3.5, y-2.5)
            cr.set_source_rgb(.12, .13, .15)
            cr.set_line_width(1.7)
            cr.stroke()
        return False

    def draw_preview(self, widget, cr):
        width, height = widget.get_allocated_width(), widget.get_allocated_height()
        if width < 90 or height < 60:
            return False
        palette = self.preview_palette()
        ink, accent = rgba(palette["text"]), rgba(palette["accent"])
        paint_gradient(cr, palette, "background", 0, 0, width, height, 13)
        rows = ("йцукенгшщзх", "фывапролджэ", "⇧ячсмитьбю⌫") if self.lang == 0 else (
                "qwertyuiop", "asdfghjkl", "⇧zxcvbnm⌫")
        margin, gap = 9, 4
        cell_w = (width - 2*margin + gap) / max(map(len, rows)) - gap
        cell_h = (height - 2*margin - 3*gap) / 4
        text = widget.create_pango_layout("")
        font = widget.get_style_context().get_font(Gtk.StateFlags.NORMAL).copy()
        font.set_absolute_size(16 * Pango.SCALE)
        text.set_font_description(font)

        def key(x, y, w, label, highlighted=False):
            paint_gradient(cr, palette, "key", x, y, w, cell_h, 5)
            rounded_path(cr, x+.6, y+.6, w-1.2, cell_h-1.2, 4.6)
            if highlighted:
                Gdk.cairo_set_source_rgba(cr, accent)
            else:
                cr.set_source_rgba(ink.red, ink.green, ink.blue, .11)
            cr.set_line_width(1.7 if highlighted else .85)
            cr.stroke()
            text.set_text(label, -1)
            tw, th = text.get_pixel_size()
            cr.move_to(x+(w-tw)/2, y+(cell_h-th)/2)
            Gdk.cairo_set_source_rgba(cr, ink)
            PangoCairo.show_layout(cr, text)

        for r, row in enumerate(rows):
            left = (width - (len(row)*(cell_w+gap)-gap)) / 2
            for i, letter in enumerate(row):
                key(left+i*(cell_w+gap), margin+r*(cell_h+gap), cell_w, letter, highlighted=r == 1 and i == 3)
        bottom = (("?123", 1.5), (",", 1), ("русский" if self.lang == 0 else "English", 5), (".", 1), ("↵", 1.5))
        unit = (width - 2*margin - (len(bottom)-1)*gap) / sum(units for _, units in bottom)
        x = margin
        font.set_absolute_size(12 * Pango.SCALE)
        text.set_font_description(font)
        for label, units in bottom:
            key(x, margin+3*(cell_h+gap), unit*units, label)
            x += unit*units+gap
        return False
