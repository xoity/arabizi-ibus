from __future__ import annotations

from dataclasses import dataclass, field

from .transliterator import TranslitLogic


@dataclass
class BufferState:
    latin_buffer: str = ""
    bypass_mode: bool = False
    show_latin_preview: bool = False
    pending_prefix: str = ""
    previous_committed_word: str = ""


@dataclass
class KeyResult:
    consumed: bool
    preedit_text: str = ""
    commit_text: str = ""
    clear_preedit: bool = False
    candidates: list[str] = field(default_factory=list)
    hide_candidates: bool = False


class Processor:
    TERMINATORS = set(" \t\n\r.,;:!?()[]{}\"/-_\\")

    def __init__(self, logic: TranslitLogic | None = None) -> None:
        self.logic = logic or TranslitLogic()
        self.state = BufferState()

    @property
    def buffer(self) -> str:
        return self.state.latin_buffer

    def set_dialect(self, dialect: str) -> KeyResult:
        self.logic.set_dialect(dialect)
        return self._preview_result(consumed=True)

    def reset(self) -> None:
        previous = self.state.previous_committed_word
        self.state = BufferState(previous_committed_word=previous)

    def focus_out(self) -> KeyResult:
        if not self.state.latin_buffer and not self.state.pending_prefix:
            return KeyResult(consumed=False, clear_preedit=True, hide_candidates=True)

        committed = self.state.pending_prefix
        if self.state.latin_buffer:
            committed += self._transliterated_word(self.state.latin_buffer)
        self._update_previous_word(committed)
        self.reset()
        return KeyResult(consumed=True, commit_text=committed, clear_preedit=True, hide_candidates=True)

    def toggle_bypass_mode(self) -> KeyResult:
        self.state.bypass_mode = not self.state.bypass_mode
        self.state.show_latin_preview = self.state.bypass_mode
        return self._preview_result(consumed=True)

    def handle_escape(self) -> KeyResult:
        if not self.state.latin_buffer:
            return KeyResult(consumed=False)
        self.state.show_latin_preview = True
        return self._preview_result(consumed=True)

    def handle_backspace(self) -> KeyResult:
        if self.state.latin_buffer:
            self.state.latin_buffer = self.state.latin_buffer[:-1]
            self.state.show_latin_preview = True
            if not self.state.latin_buffer:
                if self.state.pending_prefix:
                    return KeyResult(
                        consumed=True,
                        preedit_text=self.state.pending_prefix,
                        hide_candidates=True,
                    )
                return KeyResult(consumed=True, clear_preedit=True, hide_candidates=True)
            return self._preview_result(consumed=True)

        if self.state.pending_prefix:
            self.state.pending_prefix = ""
            return KeyResult(consumed=True, clear_preedit=True, hide_candidates=True)

        return KeyResult(consumed=False)

    def select_candidate(self, index: int) -> KeyResult:
        if self.state.bypass_mode or not self.state.latin_buffer:
            return KeyResult(consumed=False)

        candidates = self.logic.suggest_candidates(
            self.state.latin_buffer,
            previous_word=self.state.previous_committed_word,
        )
        if index < 0 or index >= len(candidates):
            return KeyResult(consumed=False)

        committed = candidates[index]
        if self.state.pending_prefix:
            committed = f"{self.state.pending_prefix}{committed}"
            self.state.pending_prefix = ""
        self.state.latin_buffer = ""
        self.state.show_latin_preview = False
        self._update_previous_word(committed)
        return KeyResult(consumed=True, commit_text=committed, clear_preedit=True, hide_candidates=True)

    def handle_char(self, char: str) -> KeyResult:
        if len(char) != 1:
            return KeyResult(consumed=False)

        if char in self.TERMINATORS:
            return self._commit_with_terminator(char)

        self.state.latin_buffer += char
        return self._preview_result(consumed=True)

    def _commit_with_terminator(self, terminator: str) -> KeyResult:
        if not self.state.latin_buffer:
            if self.state.pending_prefix and terminator == " ":
                committed = f"{self.state.pending_prefix} "
                self.state.pending_prefix = ""
                self._update_previous_word(committed)
                return KeyResult(consumed=True, commit_text=committed, clear_preedit=True, hide_candidates=True)
            return KeyResult(consumed=False)

        if (
            not self.state.bypass_mode
            and terminator in {" ", "-"}
            and self.logic.is_prefix_token(self.state.latin_buffer)
            and not self.state.pending_prefix
        ):
            self.state.pending_prefix = self.logic.prefix_for(self.state.latin_buffer)
            self.state.latin_buffer = ""
            self.state.show_latin_preview = False
            return KeyResult(consumed=True, preedit_text=self.state.pending_prefix, hide_candidates=True)

        committed = self._transliterated_word(self.state.latin_buffer)
        if self.state.pending_prefix:
            committed = f"{self.state.pending_prefix}{committed}"
            self.state.pending_prefix = ""

        self.state.latin_buffer = ""
        self.state.show_latin_preview = False
        final_commit = f"{committed}{terminator}"
        self._update_previous_word(final_commit)
        return KeyResult(consumed=True, commit_text=final_commit, clear_preedit=True, hide_candidates=True)

    def _transliterated_word(self, word: str) -> str:
        if self.state.bypass_mode:
            return word
        return self.logic.transliterate_word(word, previous_word=self.state.previous_committed_word)

    def _preview_result(self, consumed: bool) -> KeyResult:
        if not self.state.latin_buffer and not self.state.pending_prefix:
            return KeyResult(consumed=consumed, clear_preedit=True, hide_candidates=True)

        if self.state.bypass_mode or self.state.show_latin_preview:
            preview_word = self.state.latin_buffer
            candidates: list[str] = []
        else:
            preview_word = self.logic.transliterate_word(
                self.state.latin_buffer,
                previous_word=self.state.previous_committed_word,
            )
            candidates = self.logic.suggest_candidates(self.state.latin_buffer, previous_word=self.state.previous_committed_word)

        preview = f"{self.state.pending_prefix}{preview_word}" if self.state.pending_prefix else preview_word
        return KeyResult(
            consumed=consumed,
            preedit_text=preview,
            clear_preedit=not bool(preview),
            candidates=candidates,
            hide_candidates=not bool(candidates),
        )

    def _update_previous_word(self, committed: str) -> None:
        stripped = committed.strip()
        if not stripped:
            return
        parts = stripped.split()
        if parts:
            self.state.previous_committed_word = parts[-1]


class KeyProcessor(Processor):
    pass
