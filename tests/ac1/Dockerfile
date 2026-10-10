# Reuse the verified Python 3.11 AL2 dependency image, never its lab runtime.
FROM xoc-chat-mcp-ft2r1:control
USER 0:0
COPY src/ /ac1/src/
COPY tests/ /ac1/tests/
COPY manifest.json /ac1/manifest.json
USER 10001:10001
WORKDIR /ac1
ENTRYPOINT ["/opt/xoc-l2/bin/python", "-I", "-B", "/ac1/tests/ac1/run.py", "--mcp", "/opt/xoc-l2/lib/python3.11/site-packages", "--dependencies", "/ic1/dependencies", "--output", "/tmp/ac1-tests.json"]
