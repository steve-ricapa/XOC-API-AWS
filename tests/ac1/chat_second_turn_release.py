"""Code-only completion margin over the accredited timeout26 release."""
import chat_continuity_release as release

if __name__ == '__main__':
    release.PREVIOUS = release.release.OUT / 'timeout26-20261013'
    release.OUT = release.release.OUT / 'second-turn-20261015'
    release.EXPECTED = 'cb/XpKQoM/bG+5tNQ9/klMJID5DfZS/7VwNmpMvBQQA='
    release.EXPECTED_PROPERTY_DIFF = ['Code']
    release.release.EXPECTED_TIMEOUT = 26
    release.release.ALLOWED_LAMBDA_DIFF = {'Code'}
    release.main()
