"""Single-file authorized-result presentation repair, no transport changes."""
import chat_continuity_release as release

if __name__ == "__main__":
    release.PREVIOUS = release.release.OUT / 'budget-20261013'
    release.OUT = release.release.OUT / 'presentation-20261013'
    release.EXPECTED = 'ZNk/VfQvA+3ZzO8jt+QkqyWvCFXJd+Dwb0Cxj3dUA7I='
    release.FILE = 'src/shared/sophia_tool_chat.py'
    release.main()
