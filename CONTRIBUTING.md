# Contributing

## Development Setup

1. Install dependencies on Arch Linux:
   - sudo pacman -S ibus python-gobject
2. Run unit tests:
   - python -m unittest discover -s tests -v
   - python tests.py

## Code Guidelines

- Keep transliteration rules data-driven in arabizi_ibus/lexicon.json.
- Add or update tests for every rule change.
- Keep IBus-specific glue in arabizi_ibus/engine.py and logic in arabizi_ibus/transliterator.py.

## Pull Request Checklist

- [ ] New behavior is covered in tests.
- [ ] python -m unittest discover -s tests -v passes.
- [ ] python tests.py reports 100% pass rate.
- [ ] README is updated when behavior changes.
