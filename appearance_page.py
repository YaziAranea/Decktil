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
    "custom": ("Свой градиент", "Custom gradient"),
    "direction": ("Градиент", "Gradient"),
    "vertical": ("Сверху вниз", "Top to bottom"),
    "horizontal": ("Слева направо", "Left to right"),
    "backgroundStart": ("Начало фона", "Background start"),
    "backgroundEnd": ("Конец фона", "Background end"),
    "keyStart": ("Начало клавиш", "Key start"),
    "keyEnd": ("Конец клавиш", "Key end"),
    "text": ("Надписи", "Key labels"),
    "accent": ("Акцент", "Accent"),
    "preview": ("Предпросмотр клавиатуры", "Keyboard preview"),
    "apply": ("Применить", "Apply"),
    "saved": ("Сохранено", "Saved"),
    "save_error": ("Не удалось сохранить тему", "Could not save the theme"),
    "back": ("Назад", "Back"),
}

COLORS = appearance.COLORS


def rgba(value):
    colour = Gdk.RGBA()
    if not colour.parse(value):
        raise ValueError(value)
    return colour


def html_colour(colour):
    return "#" + "".join(f"{round(255 * channel):02X}" for channel in (colour.red, colour.green, colour.blue))


def rounded_path(cr, x, y, w, h, radius):
    cr.new_sub_path()
    for cx, cy, angle in ((x+w-radius, y+radius, -90), (x+w-radius, y+h-radius, 0),
                          (x+radius, y+h-radius, 90), (x+radius, y+radius, 180)):
        cr.arc(cx, cy, radius, math.radians(angle), math.radians(angle+90))
    cr.close_path()


class AppearancePage(Gtk.Box):
    def __init__(self, lang, draft, custom, back):
        super().__init__(orientation=Gtk.Orientation.VERTICAL, spacing=12)
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
        title.get_style_context().add_class("deck-heading")
        heading.pack_start(title, True, True, 0)
        self.add(heading)
        self.preview = Gtk.DrawingArea()
        self.preview.set_size_request(-1, 132)
        self.preview.get_accessible().set_name(self.t("preview"))
        self.preview.connect("draw", self.draw_preview)
        self.add(self.preview)

        grid = Gtk.Grid(row_spacing=9, column_spacing=12, column_homogeneous=True)
        self.add(grid)
        self.theme = self.combo(("default", "midnight", "lagoon", "ember", "custom"))
        self.direction = self.combo(("vertical", "horizontal"))
        self.field(grid, "preset", self.theme, 0, 0)
        self.field(grid, "direction", self.direction, 1, 0)
        self.pickers = {}
        for i, key in enumerate(COLORS):
            picker = Gtk.ColorButton()
            picker.set_title(self.t(key))
            picker.set_use_alpha(False)
            self.field(grid, key, picker, i % 2, 1 + i // 2)
            picker.connect("color-set", self.pick, key)
            self.pickers[key] = picker
        actions = Gtk.Box(spacing=10)
        self.message = Gtk.Label(xalign=0)
        self.message.set_line_wrap(True)
        actions.pack_start(self.message, True, True, 0)
        self.apply = Gtk.Button.new_with_label(self.t("apply"))
        self.apply.get_style_context().add_class("suggested-action")
        self.apply.get_style_context().add_class("deck-action")
        self.apply.connect("clicked", self.commit)
        actions.pack_end(self.apply, False, False, 0)
        self.add(actions)
        self.theme.connect("changed", self.choose_theme)
        self.direction.connect("changed", self.choose_direction)
        self.sync()

    def t(self, key):
        return COPY[key][self.lang]

    def combo(self, choices):
        combo = Gtk.ComboBoxText()
        for choice in choices:
            combo.append(choice, self.t(choice))
        return combo

    def field(self, grid, key, widget, col, row):
        pair = Gtk.Box(spacing=8)
        label = Gtk.Label(label=self.t(key), xalign=0)
        label.set_mnemonic_widget(widget)
        widget.get_accessible().set_name(self.t(key))
        widget.set_size_request(60, 32)
        pair.pack_start(label, False, False, 0)
        pair.pack_end(widget, isinstance(widget, Gtk.ComboBox), True, 0)
        grid.attach(pair, col, row, 1, 1)

    def choose_theme(self, combo):
        if self.updating:
            return
        preset = combo.get_active_id()
        if preset == "custom":
            values = self.custom or self.preview_palette()
        else:
            values = dict(appearance.PRESETS[preset])
        self.draft.update(values, preset=preset)
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
            self.theme.set_active_id(self.draft["preset"])
            self.direction.set_active_id(self.draft["direction"])
            palette = self.preview_palette()
            for key, picker in self.pickers.items():
                picker.set_rgba(rgba(palette[key]))
        finally:
            self.updating = False
        self.message.set_text("")
        self.apply.set_sensitive(self.draft != self.saved)
        self.preview.queue_draw()

    def commit(self, *_):
        try:
            self.saved = appearance.save(self.draft)
        except (OSError, ValueError) as error:
            self.message.set_text(f"{self.t('save_error')} — {error}")
            return
        self.draft.update(self.saved)
        self.sync()
        self.message.set_text(self.t("saved"))

    def preview_palette(self):
        palette = self.draft.copy()
        if self.draft["preset"] == "default":
            style = self.get_style_context()
            for key, name in (("backgroundStart", "theme_bg_color"), ("backgroundEnd", "theme_bg_color"),
                              ("keyStart", "theme_base_color"), ("keyEnd", "theme_base_color"),
                              ("text", "theme_fg_color"), ("accent", "theme_selected_bg_color")):
                found, system_colour = style.lookup_color(name)
                if found:
                    palette[key] = html_colour(system_colour)
        return palette

    def draw_preview(self, widget, cr):
        width, height = widget.get_allocated_width(), widget.get_allocated_height()
        if width < 40 or height < 32:
            return False
        palette = self.preview_palette()
        colours = {key: rgba(palette[key]) for key in COLORS}
        horizontal = self.draft["direction"] == "horizontal"

        def fill(x, y, w, h, radius, start, end):
            gradient = cairo.LinearGradient(x, y, x+w if horizontal else x, y if horizontal else y+h)
            for stop, name in ((0, start), (1, end)):
                shade = colours[name]
                gradient.add_color_stop_rgb(stop, shade.red, shade.green, shade.blue)
            rounded_path(cr, x, y, w, h, radius)
            cr.set_source(gradient)
            cr.fill()

        fill(0, 0, width, height, 10, "backgroundStart", "backgroundEnd")
        rows = ("ЙЦУКЕНГШЩЗХ", "ФЫВАПРОЛДЖЭ", "ЯЧСМИТЬБЮ.") if self.lang == 0 else (
                "QWERTYUIOP", "ASDFGHJKL", "ZXCVBNM,.")
        margin, gap = 10, 4
        cell_w = min((width - 2*margin + gap) / max(map(len, rows)), 54) - gap
        cell_h = (height - 2*margin - 2*gap) / 3
        text = widget.create_pango_layout("")
        font = Pango.FontDescription("Sans")
        font.set_absolute_size(15 * Pango.SCALE)
        text.set_font_description(font)
        for r, row in enumerate(rows):
            left = (width - (len(row)*(cell_w+gap)-gap)) / 2
            top = margin + r*(cell_h+gap)
            for i, letter in enumerate(row):
                x = left + i*(cell_w+gap)
                fill(x, top, cell_w, cell_h, 4, "keyStart", "keyEnd")
                if r == 1 and i == 3:
                    rounded_path(cr, x+1, top+1, cell_w-2, cell_h-2, 4)
                    Gdk.cairo_set_source_rgba(cr, colours["accent"])
                    cr.set_line_width(2)
                    cr.stroke()
                text.set_text(letter, -1)
                w, h = text.get_pixel_size()
                cr.move_to(x+(cell_w-w)/2, top+(cell_h-h)/2)
                Gdk.cairo_set_source_rgba(cr, colours["text"])
                PangoCairo.show_layout(cr, text)
        return False
