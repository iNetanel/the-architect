"""Custom Textual widgets shared by The Architect's screens.

Hosts:

- :class:`BlankOffCheckbox` — a :class:`~textual.widgets.Checkbox`
  variant whose off-state marker is blank (a space) rather than a dim
  ``X``. The stock Textual checkbox renders the same ``X`` glyph in
  both states and communicates on/off only through colour — which is
  easy to misread on dark themes, especially when the off-state colour
  is close to the button background. Swapping the glyph itself removes
  the ambiguity.
- :class:`BlankOffRadioButton` — the same treatment for
  :class:`~textual.widgets.RadioButton`: the filled ``●`` is shown only
  for the selected option; unselected options render an empty slot
  instead of a dim dot.
- :data:`MATRIX_GLYPHS` — the pool of katakana / half-width katakana /
  digit characters used across the app's loading animations. Mirrors
  the iconic Matrix digital-rain alphabet (half-width katakana plus a
  handful of Latin digits) so the spinner reads as "The Architect"
  rather than the generic braille spinner Textual ships with.
- :func:`next_matrix_frame` — pull the next glyph from a rotating
  sequence, seeded deterministically. Used by both the splash spinner
  and the wait-screen spinner so the animation is consistent.
- :class:`MatrixRain` — a multi-column falling-glyph widget for the
  splash screen, where there's room for a real rain effect rather
  than a single animated character.
"""

from __future__ import annotations

import random
from typing import ClassVar

from rich.text import Text
from textual.binding import Binding
from textual.content import Content
from textual.message import Message
from textual.reactive import reactive
from textual.style import Style
from textual.widgets import Checkbox, RadioButton, Static


class BlankOffCheckbox(Checkbox):
    """Checkbox that shows ``▐ ▌`` when off and ``▐X▌`` when on.

    Overrides :meth:`~textual.widgets._toggle_button.ToggleButton._button`
    to pick the inner glyph based on :attr:`value`, keeping the left and
    right bracket characters inherited from the parent class so the
    widget still looks like a Textual checkbox.
    """

    @property
    def _button(self) -> Content:
        """Build the button content, swapping the inner glyph by state.

        Reimplements the parent's :meth:`_button` body but substitutes a
        space for :attr:`BUTTON_INNER` when :attr:`value` is false. This
        is the glyph-level fix for the "dim X looks selected" problem;
        it is independent of theme colours and of any CSS overrides.
        """
        button_style = self.get_visual_style("toggle--button")
        side_style = Style(
            foreground=button_style.background,
            background=self.background_colors[1],
        )
        inner = self.BUTTON_INNER if self.value else " "
        return Content.assemble(
            (self.BUTTON_LEFT, side_style),
            (inner, button_style),
            (self.BUTTON_RIGHT, side_style),
        )


class BlankOffRadioButton(RadioButton):
    """Radio button that shows ``▐ ▌`` when off and ``▐●▌`` when on.

    Same rationale as :class:`BlankOffCheckbox`: Textual's stock
    :class:`~textual.widgets.RadioButton` renders its ``●`` glyph in
    both states and communicates selected vs unselected only through
    colour, which makes unselected options look like they still "have
    something in the box". Swapping the glyph to a space on off makes
    the single selected option unambiguous.
    """

    @property
    def _button(self) -> Content:
        """Build the button content, swapping the inner glyph by state."""
        button_style = self.get_visual_style("toggle--button")
        side_style = Style(
            foreground=button_style.background,
            background=self.background_colors[1],
        )
        inner = self.BUTTON_INNER if self.value else " "
        return Content.assemble(
            (self.BUTTON_LEFT, side_style),
            (inner, button_style),
            (self.BUTTON_RIGHT, side_style),
        )


# ── Matrix digital-rain alphabet ───────────────────────────────────────

# Half-width katakana are the canonical "Matrix rain" characters — the
# movie used mirrored katakana specifically. We add a handful of Latin
# digits because they also rained through the effect in the films and
# render reliably on any terminal font (some of the rarer katakana do
# not). Order is intentionally shuffled so the cycle looks chaotic.
MATRIX_GLYPHS: tuple[str, ...] = (
    "ｱ",
    "ｲ",
    "ｳ",
    "ｴ",
    "ｵ",
    "ｶ",
    "ｷ",
    "ｸ",
    "ｹ",
    "ｺ",
    "ｻ",
    "ｼ",
    "ｽ",
    "ｾ",
    "ｿ",
    "ﾀ",
    "ﾁ",
    "ﾂ",
    "ﾃ",
    "ﾄ",
    "ﾅ",
    "ﾆ",
    "ﾇ",
    "ﾈ",
    "ﾉ",
    "ﾊ",
    "ﾋ",
    "ﾌ",
    "ﾍ",
    "ﾎ",
    "ﾏ",
    "ﾐ",
    "ﾑ",
    "ﾒ",
    "ﾓ",
    "ﾔ",
    "ﾕ",
    "ﾖ",
    "ﾗ",
    "ﾘ",
    "ﾙ",
    "ﾚ",
    "ﾛ",
    "ﾜ",
    "ｦ",
    "ﾝ",
    "0",
    "1",
    "2",
    "3",
    "4",
    "5",
    "6",
    "7",
    "8",
    "9",
    ":",
    "・",
    "=",
    "*",
    "+",
    "-",
    "<",
    ">",
)


def next_matrix_frame(frame_index: int) -> str:
    """Return the Matrix-rain glyph for the given frame index.

    Pure function over a seeded RNG so callers can drive the spinner
    by incrementing an integer each tick — deterministic per index,
    but visually chaotic across the sequence.

    The returned string is always exactly one character wide in a
    monospace terminal (half-width katakana are CJK half-width, which
    most modern terminals render in one cell).
    """
    # Seed with the frame index so the sequence is reproducible in
    # tests and so every instance of the animation stays in sync if
    # they share a frame counter.
    rng = random.Random(frame_index * 2654435761 & 0xFFFFFFFF)
    return rng.choice(MATRIX_GLYPHS)


# ── Multi-column rain widget for the splash screen ─────────────────────


class MatrixRain(Static):
    """Compact Matrix-rain animation for the splash screen.

    Renders a fixed-size grid of falling glyphs. Each column advances
    one cell per tick; the head of a falling stream is painted bright
    (``$accent``, which the architect theme sets to brand green), the
    trailing glyphs fade through dimmer greens. Columns respawn at the
    top at random intervals so the effect never becomes periodic.

    Implementation note: this widget is deliberately a :class:`Static`
    subclass rather than a custom :class:`Widget`. An earlier revision
    returned a :class:`rich.text.Text` from :meth:`Widget.render` and,
    depending on the installed Textual version, the compositor would
    silently fail to paint the lines when the text's shape didn't exactly
    match the widget's region — leaving the rain invisible even though
    frame state was advancing. ``Static.update`` bypasses that class of
    bug entirely: Textual owns the renderable lifecycle and the widget
    paints reliably in every terminal we've tested.
    """

    # 10 FPS is the same cadence the braille spinner used. Fast enough
    # to feel animated, slow enough not to chew CPU.
    TICK_INTERVAL: ClassVar[float] = 0.1

    # Grid size. DEFAULT_CSS is generated from these values so the render
    # grid and layout dimensions stay in sync across every rain surface.
    COLS: ClassVar[int] = 20
    ROWS: ClassVar[int] = 6

    DEFAULT_CSS = """
    MatrixRain {
        /* Width is fixed to the number of columns so the wrapping
           row container can centre us predictably. `content-align`
           would only matter if width were larger than content, so
           we hard-pin it to the grid size. */
        width: __MATRIX_RAIN_COLS__;
        height: __MATRIX_RAIN_ROWS__;
        color: $accent;
    }
    """.replace("__MATRIX_RAIN_COLS__", str(COLS)).replace("__MATRIX_RAIN_ROWS__", str(ROWS))

    # Reactive so changes trigger a re-render without us calling refresh.
    frame: reactive[int] = reactive(0)

    def __init__(self, id: str | None = None) -> None:
        super().__init__("", id=id)
        # Per-column state: (head_row, stream_length). Heads start at
        # staggered rows so the first frame isn't a flat line.
        self._rng = random.Random(0x4D415452)  # b"MATR"
        self._heads: list[int] = [
            self._rng.randrange(-self.ROWS, self.ROWS) for _ in range(self.COLS)
        ]
        self._lengths: list[int] = [self._rng.randint(2, self.ROWS) for _ in range(self.COLS)]

    def on_mount(self) -> None:
        # Paint the initial frame immediately so the widget isn't blank
        # for the first 100ms before the first tick fires.
        self.update(self._build_frame())
        self.set_interval(self.TICK_INTERVAL, self._tick)

    def _tick(self) -> None:
        # Advance each column; respawn columns whose head has fallen
        # past the bottom of the visible grid plus their trail length.
        for col in range(self.COLS):
            self._heads[col] += 1
            if self._heads[col] - self._lengths[col] > self.ROWS:
                # Respawn a bit above the top so the stream "arrives"
                # rather than blinking into existence mid-grid.
                self._heads[col] = -self._rng.randint(0, self.ROWS // 2)
                self._lengths[col] = self._rng.randint(2, self.ROWS)
        self.frame += 1
        self.update(self._build_frame())

    # Kept as a public alias for the regression test that ticks the
    # widget outside of a running App.
    def render(self) -> Text:
        return self._build_frame()

    def _build_frame(self) -> Text:
        """Assemble the current frame as styled rich text.

        Each visible cell gets one of three styles:

        - Bright head: the brand green, bold.
        - Mid-trail:   same green, normal weight.
        - Fading tail: dimmed green via Rich's ``dim`` style so terminals
          that don't support 24-bit colour still render a gradient.

        Cells outside any stream's trail are blank spaces — the grid
        stays the same size every frame so the splash layout doesn't
        reflow.
        """
        rows: list[list[tuple[str, str]]] = [[(" ", "")] * self.COLS for _ in range(self.ROWS)]
        for col in range(self.COLS):
            head = self._heads[col]
            length = self._lengths[col]
            for trail in range(length):
                row = head - trail
                if 0 <= row < self.ROWS:
                    # Use a per-cell glyph that also rotates with the
                    # frame so trails shimmer instead of being static.
                    glyph = next_matrix_frame(self.frame * self.COLS + col * 7 + trail)
                    # Trail brightness: the old style used `dim $accent`
                    # / `dim $text-muted` which, on dark terminal themes,
                    # rendered as near-invisible dark green against the
                    # near-black default screen background. On a splash
                    # that has nothing else drawn around it, that made
                    # the whole rain block read as empty space. The new
                    # palette keeps every trail cell at a readable
                    # brightness — bright head, solid mid, readable
                    # tail — so the animation is visible on any dark
                    # theme without relying on the surrounding chrome
                    # for contrast.
                    if trail == 0:
                        style = "bold $accent"
                    elif trail < length - 2:
                        style = "$accent"
                    else:
                        style = "$accent-muted"
                    rows[row][col] = (glyph, style)

        text = Text()
        for row_idx, row_cells in enumerate(rows):
            for glyph, style in row_cells:
                if style:
                    # Resolve $accent / $text-muted to concrete colours
                    # via the widget's current visual style. Cheap and
                    # keeps the render honest about the active theme.
                    text.append(glyph, style=self._resolve_style(style))
                else:
                    text.append(glyph)
            if row_idx < self.ROWS - 1:
                text.append("\n")
        return text

    def _resolve_style(self, style: str) -> str:
        """Map ``$accent`` / ``$accent-muted`` tokens to concrete Rich styles.

        Rich's :class:`rich.text.Text` does not speak Textual CSS variables,
        so we substitute the tokens with literal colours before handing the
        style string back to Rich.

        Two real-world gotchas this function defends against:

        1. ``theme_variables["text-muted"]`` is ``"auto 60%"``. That is a
           valid Textual component-class fragment but *not* a valid Rich
           colour — passing it into :func:`rich.style.Style.parse` raises
           :class:`StyleSyntaxError`, which Textual silently swallows
           while painting the widget. The whole rain block then comes
           back empty even though frame state is advancing fine. We
           never emit ``$text-muted`` any more for exactly this reason.
        2. A previous palette used ``dim $accent`` / ``dim $text-muted``
           for the trail, which rendered as near-invisible dark green on
           VSCode's default dark terminal background. Bumped the trail
           to a readable mid-green (``$accent-muted`` here) so the
           animation reads on any dark theme.

        Both tokens always resolve to a plain ``#rrggbb`` hex colour by
        the time the returned string reaches Rich.
        """
        # Brand green for the head + mid-trail cells.
        accent = "#7cc800"
        # Mid-brightness green for the trail tail. Chosen to stay
        # clearly visible on a near-black terminal background without
        # competing with the bright head glyph.
        accent_muted = "#5a9400"
        try:
            theme_accent = self.app.theme_variables.get("accent")
            if theme_accent and theme_accent.startswith("#"):
                accent = theme_accent
        except Exception:
            pass
        return style.replace("$accent-muted", accent_muted).replace("$accent", accent)


# ── Matrix-style button ────────────────────────────────────────────────


class MatrixButton(Static, can_focus=True):
    """Flat, bracketed, green-on-dark button that matches the brand.

    The stock Textual :class:`~textual.widgets.Button` renders a 3D
    raised surface with rounded corners, centred label, and a default
    orange/blue palette. It reads as a modern web button, which is
    completely wrong for The Architect — the app is themed after the
    Matrix character, so buttons should look like terminal chrome
    from that era: flat, bracketed, green on black, with an
    ASCII-style focus frame instead of depth cues.

    Visual vocabulary::

        [ C ]  Continue           ← idle (dim green border, muted label)
        ▶ [ C ]  Continue         ← focused (bright green, bold label)
        [ X ]  Detach             ← disabled (muted grey, tooltip hint)

    The widget is a subclass of :class:`Static` with ``can_focus=True``
    so it participates in the Tab-order and accepts clicks. Enter and
    Space on the focused button fire the :class:`Pressed` message,
    which callers handle exactly like ``Button.Pressed``.

    Single-key mnemonics (the ``[C]`` in ``Continue [C]``) are NOT
    added by this widget — the owning screen defines Bindings for
    each key so they work regardless of which button has focus. That
    is also why the label just shows the mnemonic character inside
    the brackets: it mirrors what the user types.
    """

    DEFAULT_CSS = """
    MatrixButton {
        width: auto;
        min-width: 18;
        height: 1;
        padding: 0 1;
        margin: 0 1;
        color: $text-muted;
        background: transparent;
        text-style: none;
    }

    MatrixButton:focus {
        /* Focus = brand green label on a subtle $panel row so the
           selected button reads as "highlighted" without turning the
           whole screen green. Matches the tone of the rest of the
           app's form screens, which also use $accent only for the
           active element's text. */
        color: $accent;
        background: $panel;
        text-style: bold;
    }

    MatrixButton.-disabled {
        color: $text-muted 50%;
        text-style: none;
    }

    MatrixButton.-disabled:focus {
        color: $text-muted 50%;
        background: transparent;
        text-style: none;
    }
    """

    BINDINGS = [
        Binding("enter", "press", "Press", show=False),
        Binding("space", "press", "Press", show=False),
    ]

    class Pressed(Message):
        """Emitted when the button is activated via keyboard or click."""

        def __init__(self, button: MatrixButton) -> None:
            super().__init__()
            self.button = button

        @property
        def control(self) -> MatrixButton:
            # Matches the Textual convention used by Button.Pressed so
            # screens can treat Pressed messages uniformly.
            return self.button

    def __init__(
        self,
        label: str,
        *,
        key: str = "",
        id: str | None = None,
        disabled: bool = False,
    ) -> None:
        """Build a matrix-style button.

        Args:
            label: Text shown after the bracketed key, e.g.
                ``"Continue"``. Rendered bold when focused, dim when
                disabled.
            key: Single-character mnemonic shown inside the brackets,
                e.g. ``"C"``. Purely visual — the owning screen wires
                the actual key binding. An empty string renders an
                empty bracket (``[ ]``), useful for buttons that are
                keyboard-only via Tab/Enter.
            id: DOM id used for routing messages and CSS.
            disabled: When ``True``, the button renders muted and
                ignores Enter / Space / click. Callers flip this with
                :meth:`set_disabled` as state changes.
        """
        # Initialise our disabled flag BEFORE super().__init__ because
        # Textual's ``Widget.__init__`` may already read ``is_disabled``
        # while composing (the property getter is routed through CSS
        # matchers during mount).
        self._mb_disabled = disabled
        super().__init__("", id=id)
        self._label_text = label
        self._key = key.strip()[:1]
        # Custom class so CSS can target the disabled state without
        # relying on Textual's internal ``-disabled`` (which is
        # applied to a deeper container).
        if disabled:
            self.add_class("-disabled")
        self._refresh_content()

    # ── Public API ─────────────────────────────────────────────────────

    @property
    def is_disabled(self) -> bool:
        """True when the button is in its disabled / non-interactive state.

        This is a our own flag, not Textual's built-in ``disabled`` —
        keeping them separate avoids collisions with the framework's
        focus / events plumbing that uses ``self.disabled``.
        """
        return getattr(self, "_mb_disabled", False)

    def set_disabled(self, disabled: bool) -> None:
        """Toggle the disabled state and repaint.

        Callers use this instead of touching ``self.disabled`` directly
        so the custom ``-disabled`` class stays in sync with the
        internal flag we consult inside :meth:`action_press`.
        """
        self._mb_disabled = disabled
        if disabled:
            self.add_class("-disabled")
        else:
            self.remove_class("-disabled")
        self._refresh_content()

    # ── Internal rendering ─────────────────────────────────────────────

    def _refresh_content(self) -> None:
        """Assemble the button face: ``[ K ]  Label`` (or ``▌ [ K ]  Label``).

        We build the text by hand instead of using a second child
        widget so the whole button is a single cell-aligned line —
        ``Static`` can style it as one unit and the focus/disabled
        CSS rules apply uniformly. The focus marker is a solid block
        bar (``▌``), which reads as "this row is selected" without
        implying horizontal motion the way an arrow glyph (``▸``)
        would. Users who see ``▸`` instinctively try left/right
        instead of up/down.
        """
        key = self._key.upper() if self._key else " "
        marker = "▌ " if self.has_focus and not self.is_disabled else "  "
        self.update(f"{marker}[ {key} ]  {self._label_text}")

    def on_focus(self) -> None:
        # Re-render so the ``▸`` marker appears.
        self._refresh_content()

    def on_blur(self) -> None:
        self._refresh_content()

    # ── Interaction ────────────────────────────────────────────────────

    def action_press(self) -> None:
        """Emit :class:`Pressed` unless the button is disabled."""
        if self.is_disabled:
            return
        self.post_message(self.Pressed(self))

    def on_click(self) -> None:
        """Clicks activate the button just like Enter would."""
        if self.is_disabled:
            return
        # Focus first so the visual jumps to this button, then press.
        self.focus()
        self.post_message(self.Pressed(self))
