"""Reuse the single-file accredited release workflow for the deadline repair."""
import chat_continuity_release as previous

if __name__ == "__main__":
    previous.PREVIOUS = previous.release.OUT / 'continuity-20261013'
    previous.OUT = previous.release.OUT / 'budget-20261013'
    previous.EXPECTED = 'dgIc5H3/YFjgdwruzeDWb4xWMnb6XkAfen0UNvOuH4I='
    previous.main()
