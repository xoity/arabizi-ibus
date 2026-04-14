from __future__ import annotations

import gi

gi.require_version("IBus", "1.0")
from gi.repository import IBus

from .key_processor import KeyProcessor, KeyResult
from .transliterator import TranslitLogic
from .user_adapter import UserAdapter


class ArabiziEngine(IBus.Engine):
    __gtype_name__ = "ArabiziEngine"
    DIALECT_PROP_KEY = "dialect"
    DIALECT_PROP_PREFIX = "dialect:"

    def __init__(self, *args, **kwargs) -> None:
        super().__init__(*args, **kwargs)
        self.user_adapter = UserAdapter()
        self.processor = KeyProcessor(logic=TranslitLogic(user_adapter=self.user_adapter))
        self.lookup_table = IBus.LookupTable.new(9, 0, True, False)
        self._current_candidates: list[str] = []
        self._dialect = "default"
        self._register_properties()

    def do_focus_in(self) -> None:
        self.processor.reset()
        self._clear_preedit()
        self._hide_candidates()
        self._register_properties()

    def do_focus_out(self) -> None:
        result = self.processor.focus_out()
        self._apply_result(result)
        self.processor.reset()
        self._hide_candidates()

    def do_reset(self) -> None:
        self.processor.reset()
        self._clear_preedit()
        self._hide_candidates()

    def do_property_activate(self, prop_name: str, prop_state: int) -> None:
        del prop_state
        if not prop_name.startswith(self.DIALECT_PROP_PREFIX):
            return

        dialect = prop_name.split(":", 1)[1]
        if dialect not in self.processor.logic.available_dialects():
            return

        self._dialect = dialect
        result = self.processor.set_dialect(dialect)
        self._apply_result(result)
        self._update_dialect_property_state()

    def do_process_key_event(self, keyval: int, keycode: int, state: int) -> bool:
        del keycode

        if state & IBus.ModifierType.RELEASE_MASK:
            return False

        if self._has_passthrough_modifier(state):
            return False

        if self._is_shift_space(keyval, state):
            result = self.processor.toggle_bypass_mode()
            self._apply_result(result)
            return True

        key_name = IBus.keyval_name(keyval) or ""

        if key_name == "Escape":
            result = self.processor.handle_escape()
            self._apply_result(result)
            return result.consumed

        if self._current_candidates and key_name.isdigit() and key_name != "0":
            result = self.processor.select_candidate(int(key_name) - 1)
            if result.selected_word:
                self.user_adapter.increment_word(result.selected_word)
            self._apply_result(result)
            return result.consumed

        if key_name == "BackSpace":
            result = self.processor.handle_backspace()
            self._apply_result(result)
            return result.consumed

        char = self._keyval_to_char(keyval)
        if not char:
            return False
        if not char.isprintable():
            return False

        result = self.processor.handle_char(char)
        self._apply_result(result)
        return result.consumed

    def do_candidate_clicked(self, index: int, button: int, state: int) -> None:
        del button, state
        result = self.processor.select_candidate(index)
        if result.selected_word:
            self.user_adapter.increment_word(result.selected_word)
        self._apply_result(result)

    def _apply_result(self, result: KeyResult) -> None:
        if result.commit_text:
            self.commit_text(IBus.Text.new_from_string(result.commit_text))

        if result.candidates:
            self._show_candidates(result.candidates)
        elif result.hide_candidates:
            self._hide_candidates()

        if result.clear_preedit:
            self._clear_preedit()
            return

        if result.preedit_text:
            preview_text = result.preedit_text
            cursor_pos = len(preview_text)
            if result.ghost_text:
                preview_text = f"{preview_text}{result.ghost_text}"
            self.update_preedit_text(
                IBus.Text.new_from_string(preview_text),
                cursor_pos,
                True,
            )

    def _clear_preedit(self) -> None:
        self.update_preedit_text(IBus.Text.new_from_string(""), 0, False)

    def _show_candidates(self, candidates: list[str]) -> None:
        self.lookup_table.clear()
        self._current_candidates = list(candidates)
        for candidate in candidates:
            self.lookup_table.append_candidate(IBus.Text.new_from_string(candidate))
        self.lookup_table.set_cursor_pos(0)
        self.update_lookup_table(self.lookup_table, True)
        self.show_lookup_table()

    def _hide_candidates(self) -> None:
        self._current_candidates = []
        self.hide_lookup_table()

    def _register_properties(self) -> None:
        props = IBus.PropList.new()
        sub_props = IBus.PropList.new()

        for dialect, label in self.processor.logic.available_dialects().items():
            state = IBus.PropState.CHECKED if dialect == self._dialect else IBus.PropState.UNCHECKED
            sub_props.append(
                IBus.Property.new(
                    f"{self.DIALECT_PROP_PREFIX}{dialect}",
                    IBus.PropType.RADIO,
                    IBus.Text.new_from_string(label),
                    "",
                    IBus.Text.new_from_string("Arabizi dialect"),
                    True,
                    True,
                    state,
                    None,
                )
            )

        props.append(
            IBus.Property.new(
                self.DIALECT_PROP_KEY,
                IBus.PropType.MENU,
                IBus.Text.new_from_string("Dialect"),
                "",
                IBus.Text.new_from_string("Switch transliteration dialect"),
                True,
                True,
                IBus.PropState.UNCHECKED,
                sub_props,
            )
        )
        self.register_properties(props)

    def _update_dialect_property_state(self) -> None:
        for dialect, label in self.processor.logic.available_dialects().items():
            state = IBus.PropState.CHECKED if dialect == self._dialect else IBus.PropState.UNCHECKED
            self.update_property(
                IBus.Property.new(
                    f"{self.DIALECT_PROP_PREFIX}{dialect}",
                    IBus.PropType.RADIO,
                    IBus.Text.new_from_string(label),
                    "",
                    IBus.Text.new_from_string("Arabizi dialect"),
                    True,
                    True,
                    state,
                    None,
                )
            )

    @staticmethod
    def _keyval_to_char(keyval: int) -> str:
        value = IBus.keyval_to_unicode(keyval)
        if value is None:
            return ""
        if isinstance(value, int):
            return chr(value) if value > 0 else ""
        if isinstance(value, str):
            return value
        return ""

    @staticmethod
    def _is_shift_space(keyval: int, state: int) -> bool:
        return bool(state & IBus.ModifierType.SHIFT_MASK) and (IBus.keyval_name(keyval) == "space")

    @staticmethod
    def _has_passthrough_modifier(state: int) -> bool:
        passthrough_mask = 0
        for name in ("CONTROL_MASK", "MOD1_MASK", "MOD4_MASK", "SUPER_MASK", "META_MASK"):
            passthrough_mask |= int(getattr(IBus.ModifierType, name, 0))
        return bool(state & passthrough_mask)
