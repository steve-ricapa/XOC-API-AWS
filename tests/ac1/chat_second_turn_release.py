"""Code-only completion margin over the accredited timeout26 release."""
import chat_continuity_release as release

if __name__ == '__main__':
    release.PREVIOUS = release.release.OUT / 'uncertain-turn-accredited-20261015'
    release.OUT = release.release.OUT / 'second-turn-20261015'
    release.EXPECTED = '6hafc+Evju/duP+CKsOUwZnkZZzXDbOqiSRIS2Vucu4='
    release.EXPECTED_PROPERTY_DIFF = ['Code']
    release.release.EXPECTED_TIMEOUT = 26
    release.release.ALLOWED_LAMBDA_DIFF = {'Code'}
    release.main()
