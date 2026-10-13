"""Approved Serverless Chat timeout change with exact isolated artifact."""
import chat_continuity_release as release

if __name__ == "__main__":
    release.PREVIOUS = release.release.OUT / 'presentation-20261013'
    release.OUT = release.release.OUT / 'timeout26-20261013'
    release.EXPECTED = 'E7LEy4rv75TgEEDB3dVlXFzoXDl2IrFDAQCEyICu9E4='
    release.EXPECTED_PROPERTY_DIFF = ['Code', 'Timeout']
    release.release.EXPECTED_TIMEOUT = 26
    release.release.ALLOWED_LAMBDA_DIFF = {'Code', 'Timeout'}
    release.main()
